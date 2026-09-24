"""Validacion fuera de muestra y walk-forward.

Principios que se respetan en todo el modulo:
- Una estrategia solo puede usar datos ANTERIORES al momento en que decide. Un
  tramo de evaluacion (test) usa la historia previa como contexto para calentar
  indicadores, pero solo opera desde su inicio (`trade_start`).
- Ningun parametro se ajusta mirando el tramo de test: en walk-forward con
  re-optimizacion, el mejor conjunto se elige SOLO con la ventana de
  entrenamiento y despues se evalua, una unica vez, en la ventana siguiente.
- Cada tramo arranca con el capital inicial: las metricas de los tramos son
  comparables entre si y no dependen de lo que paso antes."""

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
from pydantic import BaseModel

from app.backtest.config import BacktestConfig
from app.backtest.engine import BacktestResult
from app.backtest.metrics import compute_metrics
from app.backtest.optimizer import MAX_COMBINATIONS, build_param_grid
from app.backtest.progress import Cancelled, Progress
from app.backtest.service import MarketData, simulate
from app.strategies.base import Strategy

MIN_SEGMENT_BARS = 50
MAX_WF_SIMULATIONS = 2500
OBJECTIVES = ["sharpe_ratio", "sortino_ratio", "total_return_pct", "profit_factor", "expectancy"]


# --------------------------------------------------------- in / out of sample

@dataclass
class Segment:
    label: str
    start: pd.Timestamp
    end: pd.Timestamp
    result: BacktestResult
    metrics: dict


def snap_split(candles: pd.DataFrame, split: dt.datetime | pd.Timestamp) -> pd.Timestamp:
    """Primera vela con timestamp >= split (el corte cae siempre sobre una vela)."""
    pos = int(candles.index.searchsorted(pd.Timestamp(split)))
    if pos >= len(candles):
        raise ValueError("La fecha de corte esta despues de la ultima vela.")
    return candles.index[pos]


def split_from_fraction(candles: pd.DataFrame, in_sample_fraction: float) -> pd.Timestamp:
    if not 0.1 <= in_sample_fraction <= 0.9:
        raise ValueError("El porcentaje in-sample debe estar entre 10% y 90%.")
    return candles.index[int(len(candles) * in_sample_fraction)]


def run_in_out_sample(
    strategy_cls: type[Strategy], params: BaseModel, config: BacktestConfig, market: MarketData,
    split: dt.datetime | pd.Timestamp,
) -> tuple[Segment, Segment]:
    """Mismos parametros en los dos tramos. In-sample: velas anteriores al corte.
    Out-of-sample: opera desde el corte, usando la historia previa solo como contexto."""
    candles = market.candles
    cut = snap_split(candles, split)
    in_bars = int((candles.index < cut).sum())
    out_bars = len(candles) - in_bars
    if in_bars < MIN_SEGMENT_BARS or out_bars < MIN_SEGMENT_BARS:
        raise ValueError(
            f"Cada tramo necesita al menos {MIN_SEGMENT_BARS} velas (in-sample: {in_bars}, out-of-sample: {out_bars}). "
            "Mové la fecha de corte o ampliá el período."
        )

    in_candles = candles[candles.index < cut]
    in_result, in_metrics = simulate(strategy_cls, params, config, market, candles=in_candles, include_quality=False)
    out_result, out_metrics = simulate(
        strategy_cls, params, config, market, candles=candles, trade_start=cut, include_quality=False
    )
    return (
        Segment("In-sample", in_candles.index[0], in_candles.index[-1], in_result, in_metrics),
        Segment("Out-of-sample", cut, candles.index[-1], out_result, out_metrics),
    )


# ------------------------------------------------------------------ walk-forward

@dataclass
class Window:
    index: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp  # exclusivo; tambien es el inicio del test
    test_start: pd.Timestamp
    test_end: pd.Timestamp  # exclusivo


@dataclass
class OptimizeSpec:
    axes: list[tuple[str, list]]
    objective: str = "sharpe_ratio"
    min_train_trades: int = 10


@dataclass
class WindowResult:
    window: Window
    params: dict
    train_metrics: dict
    test_metrics: dict
    test_result: BacktestResult | None = None
    skipped_reason: str | None = None


@dataclass
class WalkForwardResult:
    windows: list[WindowResult]
    optimized: bool
    overlapping: bool
    aggregate: dict
    stitched_metrics: dict | None = None
    stitched_result: BacktestResult | None = None
    cancelled: bool = False
    warnings: list[str] = field(default_factory=list)


def make_windows(
    start: pd.Timestamp, end: pd.Timestamp, training_days: float, testing_days: float, step_days: float
) -> list[Window]:
    """Ventanas deslizantes [train)[test) que caben enteras en [start, end)."""
    if training_days <= 0 or testing_days <= 0 or step_days <= 0:
        raise ValueError("Entrenamiento, test y paso deben ser positivos.")
    train, test, step = (pd.Timedelta(days=d) for d in (training_days, testing_days, step_days))
    windows: list[Window] = []
    k = 0
    while True:
        train_start = start + k * step
        train_end = train_start + train
        test_end = train_end + test
        if test_end > end:
            break
        windows.append(Window(k, train_start, train_end, train_end, test_end))
        k += 1
    return windows


def _objective_value(metrics: dict, objective: str) -> float:
    value = metrics.get(objective)
    return -np.inf if value is None else float(value)


def pick_by_train(points: list[dict], objective: str, min_trades: int) -> dict | None:
    """Mejor combinacion segun el objetivo, evaluado UNICAMENTE con resultados de entrenamiento."""
    valid = [p for p in points if p["metrics"].get("num_trades", 0) >= min_trades]
    if not valid:
        return None
    best = max(valid, key=lambda p: _objective_value(p["metrics"], objective))
    return best if np.isfinite(_objective_value(best["metrics"], objective)) else None


def _slice(candles: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    return candles[candles.index < end]


def stitch(results: list[BacktestResult], initial_capital: float) -> BacktestResult:
    """Une los tramos de test (sin solaparse) en una sola curva: cada tramo
    continua con el capital con el que termino el anterior."""
    factor, curves, trades, exposure = 1.0, [], [], []
    for r in results:
        scale = initial_capital / r.initial_capital * factor
        curves.append(r.equity_curve * scale)
        trades += [
            replace(t, qty=t.qty * scale, equity_before=t.equity_before * scale,
                    pnl=None if t.pnl is None else t.pnl * scale,
                    gross_pnl=None if t.gross_pnl is None else t.gross_pnl * scale,
                    entry_fee=t.entry_fee * scale, exit_fee=t.exit_fee * scale,
                    funding=t.funding * scale, slippage_cost=t.slippage_cost * scale)
            for t in r.trades
        ]
        if r.exposure is not None:
            exposure.append(r.exposure)
        factor *= r.equity_curve.iloc[-1] / r.initial_capital
    first = results[0]
    return BacktestResult(
        strategy_key=first.strategy_key, symbol=first.symbol, timeframe=first.timeframe, params=first.params,
        initial_capital=initial_capital, equity_curve=pd.concat(curves), trades=trades,
        exposure=pd.concat(exposure) if exposure else None, bar_seconds=first.bar_seconds,
        diagnostics={"open_at_end": False}, config=first.config,
    )


def _aggregate(done: list[WindowResult]) -> dict:
    tests = [w.test_metrics for w in done if w.test_metrics and "error" not in w.test_metrics]
    returns = np.array([m["total_return_pct"] for m in tests], dtype=float)
    sharpes = np.array([m["sharpe_ratio"] for m in tests if m.get("sharpe_ratio") is not None], dtype=float)
    trades = np.array([m["num_trades"] for m in tests], dtype=float)
    return {
        "n_windows": len(done),
        "n_evaluated": len(tests),
        "n_skipped": len(done) - len(tests),
        "pct_profitable": float((returns > 0).mean() * 100) if len(returns) else None,
        "return_mean": float(returns.mean()) if len(returns) else None,
        "return_median": float(np.median(returns)) if len(returns) else None,
        "return_min": float(returns.min()) if len(returns) else None,
        "return_max": float(returns.max()) if len(returns) else None,
        "return_std": float(returns.std(ddof=1)) if len(returns) > 1 else None,
        "sharpe_median": float(np.median(sharpes)) if len(sharpes) else None,
        "worst_drawdown": float(min(m["max_drawdown_pct"] for m in tests)) if tests else None,
        "trades_total": int(trades.sum()) if len(trades) else 0,
        "pct_few_trades": float((trades < 10).mean() * 100) if len(trades) else None,
    }


def run_walk_forward(
    strategy_cls: type[Strategy],
    params: BaseModel,
    config: BacktestConfig,
    market: MarketData,
    training_days: float,
    testing_days: float,
    step_days: float,
    optimize: OptimizeSpec | None = None,
    progress: Progress | None = None,
) -> WalkForwardResult:
    candles = market.candles
    bar = pd.Series(candles.index[1:] - candles.index[:-1]).median()
    windows = make_windows(candles.index[0], candles.index[-1] + bar, training_days, testing_days, step_days)
    if not windows:
        needed = training_days + testing_days
        span = (candles.index[-1] + bar - candles.index[0]).days
        raise ValueError(
            f"No entra ninguna ventana: hacen falta al menos {needed:g} dias de historia y hay {span}. "
            "Reduci el entrenamiento o el test, o ampliá la historia."
        )

    grid: list[dict] = []
    if optimize is not None:
        if not optimize.axes:
            raise ValueError("Elegi al menos un parametro para re-optimizar.")
        if optimize.objective not in OBJECTIVES:
            raise ValueError(f"Objetivo invalido: {optimize.objective}")
        base = params.model_dump()
        grid = [{**base, **combo} for combo in build_param_grid({n: v for n, v in optimize.axes})]
        if len(grid) > MAX_COMBINATIONS:
            raise ValueError(f"Son {len(grid)} combinaciones por ventana (maximo {MAX_COMBINATIONS}).")

    sims_per_window = (len(grid) if optimize else 1) + 1  # combinaciones en train + test (o train fijo + test)
    if len(windows) * sims_per_window > MAX_WF_SIMULATIONS:
        raise ValueError(
            f"Serian {len(windows) * sims_per_window:,} simulaciones (maximo {MAX_WF_SIMULATIONS:,}): "
            "usa menos ventanas (paso mas largo) o menos combinaciones."
        )
    if progress is not None:
        progress.total, progress.done = len(windows) * sims_per_window, 0

    def sim(p: BaseModel | dict, end: pd.Timestamp, start: pd.Timestamp):
        model = p if isinstance(p, BaseModel) else strategy_cls.params_model(**p)
        return simulate(strategy_cls, model, config, market, candles=_slice(candles, end), trade_start=start,
                        include_quality=False)

    done: list[WindowResult] = []
    cancelled = False
    try:
        for w in windows:
            if progress is not None:
                progress.check()
                progress.message = f"Ventana {w.index + 1} de {len(windows)}"
            chosen: dict = params.model_dump()
            skipped = None

            if optimize is not None:
                points = []
                for combo in grid:
                    _, m = sim(combo, w.train_end, w.train_start)
                    points.append({"params": combo, "metrics": m})
                    if progress is not None:
                        progress.step()
                        progress.check()
                best = pick_by_train(points, optimize.objective, optimize.min_train_trades)
                if best is None:
                    skipped = f"ninguna combinacion tuvo al menos {optimize.min_train_trades} operaciones en el entrenamiento"
                    train_metrics: dict = {}
                else:
                    chosen, train_metrics = best["params"], best["metrics"]
            else:
                _, train_metrics = sim(chosen, w.train_end, w.train_start)
                if progress is not None:
                    progress.step()

            if skipped:
                done.append(WindowResult(w, chosen, train_metrics, {}, None, skipped))
                if progress is not None:
                    progress.step()
                continue

            test_result, test_metrics = sim(chosen, w.test_end, w.test_start)
            if progress is not None:
                progress.step()
            done.append(WindowResult(w, chosen, train_metrics, test_metrics, test_result))
    except Cancelled:
        cancelled = True

    overlapping = step_days < testing_days
    aggregate = _aggregate(done)
    warnings = [
        "Cada ventana cierra a la fuerza la posicion que quede abierta al final del test (marcada 'fin del backtest').",
    ]
    stitched_metrics = stitched_result = None
    evaluated = [w for w in done if w.test_result is not None]
    if overlapping:
        warnings.append(
            f"Las ventanas de test se solapan (paso {step_days:g} < test {testing_days:g} dias): no se unen en una sola curva "
            "porque contarian dos veces las mismas operaciones. Se muestran estadisticas por ventana."
        )
    elif len(evaluated) >= 1:
        stitched_result = stitch([w.test_result for w in evaluated], config.initial_capital)
        stitched_metrics = compute_metrics(stitched_result, config.timeframe)
        aggregate["compounded_return_pct"] = stitched_metrics["total_return_pct"]
    if optimize is not None and evaluated:
        counts = Counter(tuple(sorted((k, v) for k, v in w.params.items() if k in {n for n, _ in optimize.axes})) for w in evaluated)
        aggregate["distinct_param_sets"] = len(counts)
        aggregate["most_common_share"] = counts.most_common(1)[0][1] / len(evaluated) * 100
        warnings.append(
            "Los parametros se eligen solo con el entrenamiento de cada ventana. Si cambian mucho de una ventana a otra, "
            "la estrategia no es estable frente a esos parametros."
        )
    if aggregate["pct_few_trades"] and aggregate["pct_few_trades"] > 50:
        warnings.append("Mas de la mitad de las ventanas de test tiene menos de 10 operaciones: los resultados por ventana son poco confiables.")
    if cancelled:
        warnings.append("El analisis se cancelo: se muestran solo las ventanas completadas.")

    return WalkForwardResult(done, optimize is not None, overlapping, aggregate, stitched_metrics, stitched_result,
                             cancelled, warnings)
