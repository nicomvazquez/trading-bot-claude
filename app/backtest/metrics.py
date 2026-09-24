"""Metricas de performance, riesgo, operaciones y exposicion.

Convenciones (todas explicitas para que sean verificables):
- Retornos por vela: r_t = E_t / E_(t-1) - 1, con E el equity (efectivo + no
  realizado) marcado al cierre de cada vela.
- Anualizacion con la cantidad de velas por ano de un mercado 24/7
  (`quality.PERIODS_PER_YEAR`). Tasa libre de riesgo 0 por defecto.
- Sharpe   = mean(r - rf) / std(r, ddof=1) * sqrt(N)
- Sortino  = (mean(r) - MAR) / DD * sqrt(N), con
             DD = sqrt(mean(min(r - MAR, 0)^2)) sobre TODAS las observaciones
             (desvio a la baja, Sortino & Price 1994).
- CAGR     = (E_final / E_inicial)^(1/anos) - 1, anos = dias / 365.25
- Calmar   = CAGR / |max drawdown|
- Max drawdown = min(E_t / max(E_0..E_t) - 1), incluyendo el capital inicial
  como primer maximo. Duracion: del ultimo maximo a la recuperacion (o al
  final si nunca se recupera).
- VaR / CVaR historicos por vela: VaR = -percentil(r, 1-conf); CVaR = -media
  de los retornos peores o iguales a ese percentil.
- Operacion ganadora: PnL NETO > 0 (bruto - comisiones + funding); perdedora:
  PnL neto < 0; las de PnL exactamente 0 no cuentan como ganadoras ni perdedoras.
- Metricas no calculables (varianza cero, division por cero) se devuelven como
  None, nunca como 0."""

import math

import numpy as np
import pandas as pd

from app.backtest.engine import BacktestResult
from app.backtest.quality import PERIODS_PER_YEAR

MIN_TRADES_MINIMUM = 10
MIN_TRADES_RELIABLE = 30
MIN_DAYS_REPRESENTATIVE = 30
MIN_DAYS_CAGR = 365


def _f(value) -> float | None:
    """float finito o None (JSON no admite NaN/inf)."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _streaks(flags: list[bool]) -> int:
    best = cur = 0
    for flag in flags:
        cur = cur + 1 if flag else 0
        best = max(best, cur)
    return best


def sharpe_ratio(returns: np.ndarray, periods_per_year: float, rf_per_period: float = 0.0) -> float | None:
    if len(returns) < 2:
        return None
    std = np.std(returns, ddof=1)
    if not np.isfinite(std) or std < 1e-12:
        return None
    return _f((np.mean(returns) - rf_per_period) / std * math.sqrt(periods_per_year))


def sortino_ratio(returns: np.ndarray, periods_per_year: float, mar_per_period: float = 0.0) -> float | None:
    if len(returns) < 2:
        return None
    downside = np.minimum(returns - mar_per_period, 0.0)
    dd = math.sqrt(float(np.mean(downside**2)))
    if dd < 1e-12:
        return None
    return _f((np.mean(returns) - mar_per_period) / dd * math.sqrt(periods_per_year))


def historical_var_cvar(returns: np.ndarray, confidence: float = 0.95) -> tuple[float | None, float | None]:
    """VaR y CVaR historicos (perdidas como numeros positivos, en fraccion) por vela."""
    if len(returns) < 20:
        return None, None
    q = float(np.percentile(returns, (1 - confidence) * 100))
    var = max(-q, 0.0)
    tail = returns[returns <= q]
    cvar = max(-float(tail.mean()), 0.0) if len(tail) else None
    return var, cvar


def drawdown_series(equity: np.ndarray, initial: float) -> np.ndarray:
    """E_t / max(E_0..E_t) - 1 (fraccion, <= 0), con el capital inicial como primer maximo."""
    peak = np.maximum.accumulate(np.concatenate(([initial], equity)))[1:]
    return equity / peak - 1.0


def drawdown_episodes(times: pd.DatetimeIndex, dd: np.ndarray, bar: pd.Timedelta) -> tuple[pd.Timedelta, bool]:
    """Duracion del episodio bajo el agua mas largo (ultimo maximo -> recuperacion)
    y si ese episodio termino recuperado."""
    longest, longest_recovered = pd.Timedelta(0), True
    last_peak_time = times[0] - bar
    start = None
    for k in range(len(dd)):
        if dd[k] >= 0:
            if start is not None:
                duration = times[k] - start
                if duration > longest:
                    longest, longest_recovered = duration, True
                start = None
            last_peak_time = times[k]
        elif start is None:
            start = last_peak_time
    if start is not None:
        duration = times[-1] - start
        if duration > longest:
            longest, longest_recovered = duration, False
    return longest, longest_recovered


def trade_summary(closed: list) -> dict:
    """Estadisticas de un conjunto de operaciones cerradas (todas, o un filtro
    como solo largos / solo ganadoras). PnL NETO; None cuando no es calculable."""
    pnls = np.array([t.pnl for t in closed], dtype=float)
    pcts = np.array([(t.pnl_pct or 0.0) for t in closed], dtype=float)
    n = len(closed)
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]
    gross_profit, gross_loss = float(wins.sum()), float(-losses.sum())
    avg_win = float(wins.mean()) if len(wins) else None
    avg_loss = float(losses.mean()) if len(losses) else None
    durations = [t.duration.total_seconds() / 3600 for t in closed if t.duration is not None]
    return {
        "num_trades": n,
        "win_rate_pct": len(wins) / n * 100 if n else None,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,  # sin perdidas: indefinido
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / abs(avg_loss)) if (avg_win is not None and avg_loss) else None,
        "expectancy": float(pnls.mean()) if n else None,
        "expectancy_pct": float(pcts.mean() * 100) if n else None,
        "net_pnl": float(pnls.sum()) if n else 0.0,
        "best_trade": float(pnls.max()) if n else None,
        "worst_trade": float(pnls.min()) if n else None,
        "median_trade": float(np.median(pnls)) if n else None,
        "best_trade_pct": float(pcts.max() * 100) if n else None,
        "worst_trade_pct": float(pcts.min() * 100) if n else None,
        "avg_trade_duration_hours": float(np.mean(durations)) if durations else None,
        "breakeven_trades": int((pnls == 0).sum()) if n else 0,
        "max_consecutive_wins": _streaks([p > 0 for p in pnls]),
        "max_consecutive_losses": _streaks([p < 0 for p in pnls]),
        "gross_pnl": float(sum(t.gross_pnl or 0.0 for t in closed)),
        "total_fees": float(sum(t.fees for t in closed)),
        "total_funding": float(sum(t.funding for t in closed)),
        "total_slippage_cost": float(sum(t.slippage_cost for t in closed)),
    }


def compute_metrics(
    result: BacktestResult,
    timeframe: str,
    *,
    risk_free_rate_pct: float = 0.0,
    mar_pct: float = 0.0,
    var_confidence: float = 0.95,
    data_quality: dict | None = None,
) -> dict:
    equity = result.equity_curve
    if equity.empty:
        return {"error": "No hay suficientes velas para simular (aumenta el rango de fechas)."}

    initial = float(result.initial_capital)
    values = equity.to_numpy(dtype=float)
    times = equity.index
    bar = pd.Timedelta(seconds=result.bar_seconds) if result.bar_seconds else pd.Timedelta(hours=1)
    ppy = PERIODS_PER_YEAR.get(timeframe, 8760)
    warnings: list[dict] = []

    def warn(level: str, text: str) -> None:
        warnings.append({"level": level, "text": text})

    ruined = bool((values <= 0).any())
    if ruined:
        warn("warning", "El capital llego a cero o negativo: las metricas basadas en retornos no son confiables.")

    # ---- retornos por vela
    prev = np.concatenate(([initial], values[:-1]))
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = np.where(prev > 0, values / prev - 1.0, np.nan)
    returns = returns[np.isfinite(returns)]

    # ---- performance
    final = float(values[-1])
    total_return = final / initial - 1.0
    period_days = ((times[-1] - times[0]) + bar).total_seconds() / 86400
    years = period_days / 365.25
    cagr = (final / initial) ** (1 / years) - 1.0 if final > 0 and years > 0 else (-1.0 if final <= 0 else None)
    volatility = float(np.std(returns, ddof=1) * math.sqrt(ppy)) if len(returns) > 1 else None

    # ---- riesgo
    dd = drawdown_series(values, initial)
    max_dd = float(dd.min())
    dd_duration, dd_recovered = drawdown_episodes(times, dd, bar)
    sharpe = sharpe_ratio(returns, ppy, risk_free_rate_pct / 100 / ppy)
    sortino = sortino_ratio(returns, ppy, mar_pct / 100 / ppy)
    calmar = _f(cagr / abs(max_dd)) if (cagr is not None and max_dd < 0) else None
    var, cvar = historical_var_cvar(returns, var_confidence)

    # ---- operaciones
    closed = [t for t in result.trades if t.pnl is not None]
    summary_all = trade_summary(closed)
    n = summary_all["num_trades"]
    gross_profit = sum(t.pnl for t in closed if t.pnl > 0)

    def side_stats(side: str) -> dict:
        s = trade_summary([t for t in closed if t.side == side])
        return {
            f"{side}_trades": s["num_trades"], f"{side}_pnl": s["net_pnl"],
            f"{side}_win_rate_pct": s["win_rate_pct"], f"{side}_avg_pnl": s["expectancy"],
            f"{side}_profit_factor": s["profit_factor"],
        }

    # ---- exposicion
    if result.exposure is not None and len(result.exposure):
        exp = result.exposure.to_numpy()
        exposure_pct = float((exp != 0).mean() * 100)
        long_exposure = float((exp == 1).mean() * 100)
        short_exposure = float((exp == -1).mean() * 100)
    else:
        exposure_pct = long_exposure = short_exposure = None

    metrics = {
        # performance
        "initial_equity": initial,
        "final_equity": final,
        "total_return_pct": total_return * 100,
        "cagr_pct": cagr * 100 if cagr is not None else None,
        "annualized_volatility_pct": volatility * 100 if volatility is not None else None,
        "period_days": period_days,
        "cagr_representative": period_days >= MIN_DAYS_CAGR,
        # riesgo
        "max_drawdown_pct": max_dd * 100,
        "max_drawdown_duration_days": dd_duration.total_seconds() / 86400,
        "max_drawdown_recovered": dd_recovered,
        "currently_underwater": bool(dd[-1] < 0),
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "var_pct": var * 100 if var is not None else None,
        "cvar_pct": cvar * 100 if cvar is not None else None,
        "var_confidence": var_confidence,
        # operaciones
        **summary_all,
        **side_stats("long"),
        **side_stats("short"),
        # exposicion
        "exposure_pct": exposure_pct,
        "long_exposure_pct": long_exposure,
        "short_exposure_pct": short_exposure,
    }
    metrics = {k: (_f(v) if isinstance(v, (float, np.floating)) else v) for k, v in metrics.items()}

    # ---- advertencias
    if n == 0:
        warn("warning", "La estrategia no cerro ninguna operacion en el periodo.")
    elif n < MIN_TRADES_MINIMUM:
        warn("warning", f"Solo {n} operaciones: muestra insuficiente, las metricas no son estadisticamente significativas.")
    elif n < MIN_TRADES_RELIABLE:
        warn("info", f"{n} operaciones: muestra chica (menos de {MIN_TRADES_RELIABLE}); las metricas tienen alta incertidumbre.")
    if period_days < MIN_DAYS_REPRESENTATIVE:
        warn("warning", f"Periodo muy corto ({period_days:.0f} dias): las metricas anualizadas no son representativas.")
    elif period_days < MIN_DAYS_CAGR:
        warn("info", f"CAGR y Calmar se anualizan a partir de {period_days:.0f} dias (menos de un ano): extrapolan el ritmo del periodo y no son representativos.")
    if sharpe is None or sortino is None:
        warn("info", "Sharpe/Sortino no calculables: los retornos no tienen variacion a la baja o hay muy pocas velas.")
    if summary_all["profit_factor"] is None and n and gross_profit > 0:
        warn("info", "Profit factor indefinido: no hubo operaciones perdedoras.")
    if metrics["currently_underwater"] and not dd_recovered:
        warn("info", "El drawdown mas largo no se recupero antes de terminar el periodo.")
    diag = result.diagnostics or {}
    if diag.get("open_at_end"):
        warn("info", "Habia una posicion abierta al final: se cerro al ultimo precio (marcada 'fin del backtest') y cuenta como operacion.")
    if diag.get("capped_by_leverage"):
        warn("warning", f"{diag['capped_by_leverage']} operaciones fueron limitadas por el apalancamiento/tamano maximo: su riesgo real fue menor al configurado.")
    skipped = diag.get("skipped_signals") or {}
    if skipped:
        detail = ", ".join(f"{k}: {v}" for k, v in skipped.items())
        warn("info", f"Senales ignoradas ({detail}).")
    if diag.get("limit_orders_expired"):
        warn("info", f"{diag['limit_orders_expired']} ordenes limit vencieron sin ejecutarse (operaciones que se perdieron).")
    if diag.get("intrabar_unavailable"):
        warn("info", f"{diag['intrabar_unavailable']} velas con stop y take-profit en la misma vela no pudieron resolverse con datos intravela: se asumio stop primero.")
    cfg = (result.config or {}).get("execution", {})
    if cfg.get("execution_model") == "same_close":
        warn("warning", "Ejecucion en el cierre de la misma vela que genero la senal: es optimista (en la practica se ejecuta despues).")
    if not any((cfg.get("slippage_bps"), cfg.get("spread_bps"))) and cfg.get("funding_mode", "none") == "none":
        warn("info", "Sin slippage, spread ni funding: los resultados son optimistas. Probalos en Analisis de costos.")
    if data_quality:
        for text in data_quality.get("warnings", []):
            warn("warning", text)

    metrics["warnings"] = warnings
    return metrics
