import datetime as dt

TIMEFRAME_OPTIONS = {"1": "1 min", "5": "5 min", "15": "15 min", "60": "1 hora", "240": "4 horas", "D": "1 día"}

METRIC_LABELS = {
    "total_return_pct": "Retorno total (%)",
    "initial_equity": "Capital inicial (USD)",
    "final_equity": "Capital final (USD)",
    "cagr_pct": "CAGR (%)",
    "annualized_volatility_pct": "Volatilidad anualizada (%)",
    "max_drawdown_pct": "Drawdown máx. (%)",
    "max_drawdown_duration_days": "Duración drawdown (días)",
    "sharpe_ratio": "Sharpe",
    "sortino_ratio": "Sortino",
    "calmar_ratio": "Calmar",
    "var_pct": "VaR 95% por vela (%)",
    "cvar_pct": "CVaR 95% por vela (%)",
    "num_trades": "Operaciones",
    "win_rate_pct": "Win rate (%)",
    "profit_factor": "Profit factor",
    "avg_win": "Ganancia prom. (USD)",
    "avg_loss": "Pérdida prom. (USD)",
    "expectancy": "Expectativa (USD)",
    "best_trade": "Mejor operación (USD)",
    "worst_trade": "Peor operación (USD)",
    "median_trade": "Operación mediana (USD)",
    "avg_trade_duration_hours": "Duración prom. de operación",
    "exposure_pct": "Tiempo en mercado (%)",
    "long_exposure_pct": "Exposición long (%)",
    "short_exposure_pct": "Exposición short (%)",
    "gross_pnl": "PnL bruto (USD)",
    "total_fees": "Comisiones (USD)",
    "total_funding": "Funding (USD)",
    "total_slippage_cost": "Costo de slippage/spread (USD)",
}

METRIC_GROUPS = {
    "Performance": ["initial_equity", "final_equity", "total_return_pct", "cagr_pct", "annualized_volatility_pct"],
    "Risk": [
        "max_drawdown_pct", "max_drawdown_duration_days", "sharpe_ratio", "sortino_ratio",
        "calmar_ratio", "var_pct", "cvar_pct",
    ],
    "Trades": [
        "num_trades", "win_rate_pct", "profit_factor", "avg_win", "avg_loss", "expectancy",
        "best_trade", "worst_trade", "median_trade", "avg_trade_duration_hours",
    ],
    "Exposure": ["exposure_pct", "long_exposure_pct", "short_exposure_pct"],
    "Costos": ["gross_pnl", "total_fees", "total_funding", "total_slippage_cost"],
}

METRIC_HELP = {
    "initial_equity": "Capital con el que arranca la simulación.",
    "final_equity": "Capital al final, ya descontadas todas las comisiones, el slippage y el funding.",
    "total_return_pct": "Ganancia o pérdida total sobre el capital inicial.",
    "cagr_pct": "Retorno anualizado: proyecta el ritmo del período a un año. Con períodos menores a un año extrapola y no es representativo.",
    "annualized_volatility_pct": "Desvío estándar de los retornos por vela, anualizado.",
    "max_drawdown_pct": "Mayor caída del capital desde un máximo hasta el mínimo siguiente, medida al cierre de cada vela.",
    "max_drawdown_duration_days": "Tiempo del episodio bajo el agua más largo: desde el último máximo hasta recuperarlo (o hasta el final si no se recuperó).",
    "sharpe_ratio": "Retorno medio por vela sobre su desvío, anualizado (tasa libre de riesgo 0). Más de 1 es aceptable y más de 2 muy bueno, pero con pocas operaciones puede engañar.",
    "sortino_ratio": "Como el Sharpe, pero el denominador es el desvío a la baja (solo cuenta la volatilidad negativa).",
    "calmar_ratio": "CAGR dividido por el drawdown máximo. Depende del CAGR: no es representativo en períodos cortos.",
    "var_pct": "Pérdida por vela que se supera solo el 5% de las veces (VaR histórico al 95%).",
    "cvar_pct": "Pérdida promedio en el 5% peor de las velas (CVaR / Expected Shortfall al 95%).",
    "num_trades": "Operaciones cerradas (incluye la que se cierra a la fuerza al final, si la hubo).",
    "win_rate_pct": "Porcentaje de operaciones con PnL neto positivo. Por sí solo no dice nada: importa junto al tamaño de ganancias y pérdidas.",
    "profit_factor": "Ganancias brutas divididas por pérdidas brutas. Mayor a 1 significa que gana más de lo que pierde.",
    "avg_win": "PnL neto promedio de las operaciones ganadoras.",
    "avg_loss": "PnL neto promedio de las operaciones perdedoras.",
    "expectancy": "PnL neto promedio por operación, en USD.",
    "best_trade": "Mayor PnL neto de una operación.",
    "worst_trade": "Menor PnL neto de una operación.",
    "median_trade": "PnL neto mediano: la operación típica, menos sensible a valores extremos que el promedio.",
    "avg_trade_duration_hours": "Tiempo promedio entre la entrada y la salida (resolución de una vela).",
    "exposure_pct": "Porcentaje de las velas con una posición abierta.",
    "long_exposure_pct": "Porcentaje de las velas con una posición larga abierta.",
    "short_exposure_pct": "Porcentaje de las velas con una posición corta abierta.",
    "gross_pnl": "Suma de PnL antes de comisiones y funding.",
    "total_fees": "Comisiones de entrada y salida pagadas.",
    "total_funding": "Funding neto: negativo si se pagó, positivo si se cobró.",
    "total_slippage_cost": "Costo estimado del slippage y el spread, ya incluido en los precios de ejecución (informativo).",
}

EXIT_REASON_LABELS = {
    "stop_loss": "Stop loss",
    "take_profit": "Take profit",
    "senal": "Señal",
    "flip": "Flip de posición",
    "fin del backtest": "Fin del backtest",
    "stop_loss_o_take_profit (exchange)": "Stop/TP del exchange",  # cierre en vivo que hizo el exchange solo
}

POS_CLASS = "text-[#006300]"
NEG_CLASS = "text-[#d03b3b]"

_USD_KEYS = {"initial_equity", "final_equity"}
_SIGNED_USD_KEYS = {
    "avg_win", "avg_loss", "expectancy", "best_trade", "worst_trade", "median_trade",
    "gross_pnl", "total_fees", "total_funding", "total_slippage_cost",
}
_SIGNED_PCT_KEYS = {"total_return_pct", "cagr_pct"}
_PCT_KEYS = {"max_drawdown_pct", "var_pct", "cvar_pct", "annualized_volatility_pct"}
_PCT1_KEYS = {"win_rate_pct", "exposure_pct", "long_exposure_pct", "short_exposure_pct"}


def fmt_usd(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}${abs(value):,.2f}"


def fmt_pct(value: float | None, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}{abs(value):,.{digits}f}%"


def fmt_hours(hours: float | None) -> str:
    if hours is None:
        return "—"
    if hours >= 48:
        return f"{hours / 24:.1f} días"
    if hours >= 1:
        return f"{hours:.1f} h"
    return f"{hours * 60:.0f} min"


def fmt_duration(delta: dt.timedelta | None) -> str:
    if delta is None:
        return "—"
    hours = delta.total_seconds() / 3600
    return fmt_hours(hours)


def sign_class(value: float | None) -> str:
    if value is None or value == 0:
        return ""
    return POS_CLASS if value > 0 else NEG_CLASS


def format_metric(key: str, value, metrics: dict | None = None) -> str:
    if value is None:
        if key == "profit_factor" and metrics and metrics.get("num_trades") and metrics.get("avg_loss") is None:
            return "∞ (sin pérdidas)"
        return "—"
    if key in _USD_KEYS:
        return fmt_usd(value)
    if key in _SIGNED_USD_KEYS:
        return fmt_usd(value, signed=key not in ("total_fees", "total_slippage_cost"))
    if key in _SIGNED_PCT_KEYS:
        return fmt_pct(value, signed=True)
    if key in _PCT_KEYS:
        return fmt_pct(value)
    if key in _PCT1_KEYS:
        return fmt_pct(value, digits=1)
    if key == "max_drawdown_duration_days":
        return f"{value:.1f} días"
    if key == "avg_trade_duration_hours":
        return fmt_hours(value)
    if key == "num_trades":
        return f"{int(value)}"
    return f"{value:.2f}"
