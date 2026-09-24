"""Orquesta un backtest completo a partir de una BacktestConfig: datos,
simulacion y metricas. La UI y los modulos de robustez/validacion comparten
este camino para que todos midan exactamente lo mismo."""

import asyncio
import datetime as dt
import hashlib
import inspect
from dataclasses import dataclass, field

import pandas as pd
from pydantic import BaseModel

from app.backtest.config import BacktestConfig
from app.backtest.data import load_candles
from app.backtest.engine import Backtester, BacktestResult
from app.backtest.extras import enrich_candles
from app.backtest.funding import get_funding_rates
from app.backtest.metrics import compute_metrics
from app.strategies.base import Strategy

# Timeframe menor usado para resolver stop/take-profit ambiguos dentro de una vela.
INTRABAR_TIMEFRAME = {"5": "1", "15": "1", "30": "5", "60": "5", "120": "15", "240": "15", "360": "60", "720": "60", "D": "60"}


@dataclass
class MarketData:
    candles: pd.DataFrame
    quality: dict
    start: dt.datetime
    end: dt.datetime
    funding: pd.Series | None = None
    intrabar: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class BacktestOutput:
    result: BacktestResult
    metrics: dict
    config: BacktestConfig
    market: MarketData
    strategy_version: str = ""


def strategy_fingerprint(strategy_cls: type[Strategy]) -> str:
    """Version declarada + hash corto del codigo: si la logica cambia, cambia la huella."""
    try:
        digest = hashlib.sha1(inspect.getsource(inspect.getmodule(strategy_cls)).encode()).hexdigest()[:10]
    except (OSError, TypeError):
        digest = "n/d"
    return f"v{strategy_cls.version}+{digest}"


def resolve_period(config: BacktestConfig) -> tuple[dt.datetime, dt.datetime]:
    end = config.end or dt.datetime.now(dt.timezone.utc)
    start = config.start or end - dt.timedelta(days=config.days)
    return start, end


async def prepare_market_data(config: BacktestConfig, strategy_cls: type[Strategy] | None = None) -> MarketData:
    """Descarga (o toma del cache) velas, funding e intravela segun la configuracion. Si la estrategia
    declara `required_data`, tambien agrega esas columnas (funding, open interest) a las velas."""
    config.validate_or_raise()
    start, end = resolve_period(config)
    candles, quality = await load_candles(config.symbol, config.timeframe, start, end)
    market = MarketData(candles=candles, quality=quality.to_dict(), start=start, end=end)
    if candles.empty:
        return market

    required = tuple(getattr(strategy_cls, "required_data", ()) or ())
    if required:
        market.candles, extra_notes = await enrich_candles(candles, config.symbol, config.timeframe, required, start, end)
        market.notes.extend(extra_notes)

    if config.execution.funding_mode == "historical":
        market.funding = await get_funding_rates(config.symbol, start, end)
        if market.funding.empty:
            market.notes.append("No se pudo obtener el funding historico: se simulo sin funding.")
    if config.execution.intrabar_resolution:
        lower = INTRABAR_TIMEFRAME.get(config.timeframe)
        if lower is None:
            market.notes.append("No hay un timeframe menor para resolver velas ambiguas en este timeframe.")
        else:
            market.intrabar, _ = await load_candles(config.symbol, lower, start, end)
    return market


def simulate(
    strategy_cls: type[Strategy],
    params: BaseModel,
    config: BacktestConfig,
    market: MarketData,
    *,
    candles: pd.DataFrame | None = None,
    trade_start: dt.datetime | None = None,
    include_quality: bool = True,
) -> tuple[BacktestResult, dict]:
    """Corre UNA simulacion (CPU pura, sin I/O). Cada corrida usa una instancia
    nueva de la estrategia: las que guardan estado (ICT) no se contaminan."""
    backtester = Backtester(
        execution=config.execution, risk=config.risk,
        funding_rates=market.funding, intrabar_candles=market.intrabar,
    )
    result = backtester.run(
        strategy_cls(params), market.candles if candles is None else candles,
        config.initial_capital, trade_start=trade_start,
    )
    result.symbol, result.timeframe, result.config = config.symbol, config.timeframe, config.to_dict()
    metrics = compute_metrics(result, config.timeframe, data_quality=market.quality if include_quality else None)
    return result, metrics


async def run_backtest(strategy_cls: type[Strategy], params: BaseModel, config: BacktestConfig) -> BacktestOutput:
    market = await prepare_market_data(config, strategy_cls)
    if market.candles.empty:
        raise ValueError("No se encontraron velas para ese simbolo y rango.")
    result, metrics = await asyncio.to_thread(simulate, strategy_cls, params, config, market)
    for note in market.notes:
        metrics.setdefault("warnings", []).append({"level": "info", "text": note})
    return BacktestOutput(result, metrics, config, market, strategy_fingerprint(strategy_cls))
