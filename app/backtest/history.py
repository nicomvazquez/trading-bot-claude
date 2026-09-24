"""Historial de backtests: guardar una corrida completa (configuracion, operaciones y curva de capital), reconstruirla
y compararla con otras. Es puro (sin base de datos ni interfaz) para poder testearlo.

Una corrida guardada permite: verla otra vez tal cual, clonar su configuracion, compararla con otras y descargarla.
La curva de capital se guarda con resolucion reducida si es muy larga (ver MAX_EQUITY_POINTS): alcanza para
verla y compararla, pero no para recalcular metricas de riesgo, que se guardan aparte ya calculadas."""

import dataclasses
import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.backtest.config import BacktestConfig
from app.backtest.engine import BacktestResult, TradeRecord
from app.backtest.quality import BAR_SECONDS

MAX_EQUITY_POINTS = 4000
_TIME_FIELDS = {"entry_time", "exit_time"}
_TRADE_FIELDS = [f.name for f in dataclasses.fields(TradeRecord)]


# ------------------------------------------------------------------ serializacion

def json_safe(value: Any) -> Any:
    """Convierte a algo que JSON acepta: numpy a tipos nativos, NaN/inf a None, fechas a texto ISO."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (np.floating, np.integer, np.bool_)):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dt.datetime, pd.Timestamp)):
        return value.isoformat()
    return value


def serialize_trades(trades: list[TradeRecord]) -> list[dict]:
    return [{name: json_safe(getattr(t, name)) for name in _TRADE_FIELDS} for t in trades]


def deserialize_trades(rows: list[dict]) -> list[TradeRecord]:
    trades = []
    for row in rows:
        values = {k: row.get(k) for k in _TRADE_FIELDS if k in row}
        for name in _TIME_FIELDS:
            values[name] = pd.Timestamp(values[name]) if values.get(name) else None
        trades.append(TradeRecord(**values))
    return trades


def serialize_equity(curve: pd.Series, max_points: int = MAX_EQUITY_POINTS) -> dict:
    """Curva de capital como {t: [ms epoch], v: [capital], stride}. Si tiene mas de `max_points` puntos se toma uno de
    cada `stride` (siempre se conserva el ultimo)."""
    index = pd.DatetimeIndex(curve.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    values = curve.to_numpy(dtype=float)
    stride = max(1, math.ceil(len(values) / max_points))
    keep = np.arange(0, len(values), stride)
    if keep[-1] != len(values) - 1:
        keep = np.append(keep, len(values) - 1)
    return {
        "t": [int(index[i].timestamp() * 1000) for i in keep],
        "v": [round(float(values[i]), 6) for i in keep],
        "stride": stride,
        "bars": len(values),
    }


def deserialize_equity(payload: dict) -> pd.Series:
    index = pd.DatetimeIndex(pd.to_datetime(payload["t"], unit="ms", utc=True))
    return pd.Series(payload["v"], index=index, dtype=float)


def run_fields(output, params: dict) -> dict:
    """Campos para crear la fila del historial a partir del resultado de un backtest (BacktestOutput)."""
    config = output.config
    return {
        "strategy_key": output.result.strategy_key, "symbol": config.symbol, "timeframe": config.timeframe,
        "params": json_safe(params), "start_date": output.market.start, "end_date": output.market.end,
        "metrics": json_safe(output.metrics), "strategy_version": output.strategy_version, "exchange": config.exchange,
        "initial_capital": config.initial_capital, "config": json_safe(config.to_dict()),
        "trades": serialize_trades(output.result.trades), "equity": serialize_equity(output.result.equity_curve),
    }


# ------------------------------------------------------------------ reconstruccion

def has_full_data(run) -> bool:
    """Corridas guardadas antes de esta version solo tienen parametros y metricas."""
    return getattr(run, "config", None) is not None


def config_from_run(run) -> BacktestConfig:
    """Configuracion de la corrida. Las viejas (sin config completa) se reconstruyen con lo que se guardo y valores por
    defecto para el resto: sirve para volver a correrlas, no para reproducirlas exactamente."""
    if getattr(run, "config", None):
        config = BacktestConfig.from_dict(run.config)
        if (config.start is None or config.end is None) and run.start_date and run.end_date:
            # corrida hecha con "N dias hacia atras": al clonarla se repite exactamente el mismo tramo de fechas
            config.start, config.end = run.start_date, run.end_date
        return config
    days = max(1, int((run.end_date - run.start_date).total_seconds() // 86400)) if run.start_date and run.end_date else 90
    return BacktestConfig(symbol=run.symbol, timeframe=run.timeframe, days=days, start=run.start_date, end=run.end_date)


def result_from_run(run) -> BacktestResult | None:
    """BacktestResult reconstruido (operaciones y curva de capital). None si la corrida no guardo esos datos.
    Requiere que `run.trades` y `run.equity` esten cargados."""
    if not run.equity or run.trades is None:
        return None
    config = config_from_run(run)
    return BacktestResult(
        strategy_key=run.strategy_key, symbol=run.symbol, timeframe=run.timeframe, params=run.params or {},
        initial_capital=run.initial_capital or config.initial_capital, equity_curve=deserialize_equity(run.equity),
        trades=deserialize_trades(run.trades), exposure=None, diagnostics={}, config=config.to_dict(),
        bar_seconds=float(BAR_SECONDS.get(run.timeframe, 3600)),
    )


# ------------------------------------------------------------------ comparacion

CONFIG_LABELS = {
    "strategy_key": "Estrategia", "strategy_version": "Versión de la estrategia", "exchange": "Exchange", "symbol": "Símbolo",
    "timeframe": "Timeframe", "days": "Días de historia", "initial_capital": "Capital inicial (USD)",
    "risk.sizing_mode": "Cálculo del tamaño", "risk.risk_per_trade_pct": "Riesgo por operación (%)",
    "risk.notional_pct_of_equity": "Nocional (% del equity)", "risk.max_leverage": "Apalancamiento máx. (x)",
    "risk.max_position_pct_of_equity": "Posición máx. (% del equity)", "risk.risk_includes_costs": "El riesgo incluye costos",
    "execution.execution_model": "Modelo de ejecución", "execution.order_type": "Tipo de orden", "execution.limit_ttl_bars": "Vigencia limit (velas)",
    "execution.taker_fee_pct": "Fee taker (%)", "execution.maker_fee_pct": "Fee maker (%)", "execution.slippage_bps": "Slippage (bps)",
    "execution.spread_bps": "Spread (bps)", "execution.funding_mode": "Funding", "execution.funding_rate_pct": "Funding constante (%/8h)",
    "execution.intrabar_resolution": "Resolución intravela", "execution.stop_slippage_bps": "Slippage extra en stops (bps)",
    "execution.funding_adverse": "Funding siempre en contra",
    "validation.mc_method": "Método Monte Carlo", "validation.mc_sims": "Simulaciones Monte Carlo", "validation.mc_seed": "Semilla Monte Carlo",
    "validation.mc_block_size": "Bloque Monte Carlo", "validation.mc_ruin_threshold_pct": "Umbral de drawdown MC (%)",
}
_CONFIG_ORDER = list(CONFIG_LABELS)


@dataclass
class RunView:
    """Lo minimo de una corrida para compararla: nombre, configuracion aplanada y metricas."""
    id: int
    name: str
    flat: dict[str, Any] = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)


def flatten(data: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (data or {}).items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, name + "."))
        else:
            out[name] = value
    return out


def run_view(run, name: str) -> RunView:
    cfg = run.config or {}
    flat = flatten({k: v for k, v in cfg.items() if k not in ("start", "end")})
    flat.setdefault("symbol", run.symbol)
    flat.setdefault("timeframe", run.timeframe)
    flat["strategy_key"] = run.strategy_key
    flat["strategy_version"] = getattr(run, "strategy_version", None)
    if run.initial_capital is not None:
        flat["initial_capital"] = run.initial_capital
    flat.update({f"params.{k}": v for k, v in (run.params or {}).items()})
    return RunView(run.id, name, flat, run.metrics or {})


def config_diff(views: list[RunView]) -> list[dict]:
    """Ajustes que DIFIEREN entre las corridas (los iguales no se muestran). Cada fila: {key, label, values}."""
    keys = {k for v in views for k in v.flat}
    rows = []
    for key in keys:
        values = [v.flat.get(key) for v in views]
        if len({repr(x) for x in values}) > 1:
            label = CONFIG_LABELS.get(key) or (f"Parámetro · {key.split('.', 1)[1]}" if key.startswith("params.") else key)
            rows.append({"key": key, "label": label, "values": values})
    rows.sort(key=lambda r: (0, _CONFIG_ORDER.index(r["key"])) if r["key"] in _CONFIG_ORDER else (1, r["key"]))
    return rows


def metric_rows(views: list[RunView], groups: dict[str, list[str]], labels: dict[str, str]) -> list[dict]:
    """Metricas de cada corrida lado a lado, en el orden en que se eligieron (sin ranking)."""
    return [
        {"group": group, "key": key, "label": labels.get(key, key), "values": [v.metrics.get(key) for v in views]}
        for group, keys in groups.items() for key in keys if any(key in v.metrics for v in views)
    ]


def equity_pct(curve: pd.Series, initial: float) -> pd.Series:
    """Curva expresada como retorno acumulado (%) sobre el capital inicial: permite comparar corridas de distinto capital."""
    return (curve / initial - 1.0) * 100.0


def cost_summary(config: BacktestConfig) -> str:
    e = config.execution
    funding = {"none": "sin funding", "constant": f"funding {e.funding_rate_pct:g}%", "historical": "funding hist."}.get(e.funding_mode, e.funding_mode)
    return f"fee {e.taker_fee_pct:g}% · slip {e.slippage_bps:g} bps · {funding}"
