"""Datos de mercado ademas de las velas (funding y open interest) y su union con las velas.

Regla anti look-ahead: a cada vela se le asigna el ultimo dato cuyo instante es <= la APERTURA
de esa vela (es decir, algo que ya se conocia antes de que la vela empezara). La estrategia decide
al cierre de la vela, asi que nunca ve un dato posterior al momento en que se decide. Es una
regla conservadora: puede dejar el dato una vela "viejo", pero nunca adelantado."""

import datetime as dt

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.backtest.funding import get_funding_rates
from app.db.base import async_session
from app.db.models import OpenInterest
from app.exchange.bybit_client import bybit_client

# timeframe de vela -> intervalo de open interest que ofrece Bybit
OI_INTERVAL = {
    "1": "5min", "3": "5min", "5": "5min", "15": "15min", "30": "30min",
    "60": "1h", "120": "1h", "240": "4h", "360": "4h", "720": "4h", "D": "1d",
}
_OI_DELTA = {
    "5min": dt.timedelta(minutes=5), "15min": dt.timedelta(minutes=15), "30min": dt.timedelta(minutes=30),
    "1h": dt.timedelta(hours=1), "4h": dt.timedelta(hours=4), "1d": dt.timedelta(days=1),
}
FUNDING_MAX_AGE = dt.timedelta(hours=9)  # el funding mas comun se liquida cada 8 h


async def get_open_interest(symbol: str, interval: str, start: dt.datetime, end: dt.datetime) -> pd.Series:
    """Open interest historico (cacheado en la base), indexado por instante."""
    delta = _OI_DELTA[interval]
    async with async_session() as session:
        min_ts, max_ts, count = (
            await session.execute(
                select(func.min(OpenInterest.timestamp), func.max(OpenInterest.timestamp), func.count()).where(
                    OpenInterest.symbol == symbol, OpenInterest.interval == interval,
                    OpenInterest.timestamp >= start, OpenInterest.timestamp <= end,
                )
            )
        ).one()

    if not count or min_ts > start + 2 * delta or max_ts < end - 2 * delta:
        fetched = await bybit_client.get_open_interest_history(symbol, interval, start, end)
        for i in range(0, len(fetched), 2500):  # asyncpg limita los parametros por sentencia
            chunk = fetched[i:i + 2500]
            async with async_session() as session:
                stmt = pg_insert(OpenInterest).values(
                    [{"symbol": symbol, "interval": interval, "timestamp": r["timestamp"], "value": r["value"]} for r in chunk]
                )
                await session.execute(stmt.on_conflict_do_nothing(index_elements=["symbol", "interval", "timestamp"]))
                await session.commit()

    async with async_session() as session:
        rows = (
            await session.execute(
                select(OpenInterest.timestamp, OpenInterest.value)
                .where(OpenInterest.symbol == symbol, OpenInterest.interval == interval,
                       OpenInterest.timestamp >= start, OpenInterest.timestamp <= end)
                .order_by(OpenInterest.timestamp)
            )
        ).all()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series([r[1] for r in rows], index=pd.DatetimeIndex([r[0] for r in rows], tz="UTC"), dtype=float)


def attach_series(
    candles: pd.DataFrame, series: pd.Series | None, column: str, max_age: dt.timedelta
) -> pd.DataFrame:
    """Agrega `series` como columna `column`: a cada vela le toca el ultimo valor con instante <= su apertura,
    siempre que no tenga mas de `max_age` (si es mas viejo queda NaN: no se rellenan huecos largos)."""
    out = candles.copy()
    if series is None or series.empty:
        out[column] = float("nan")
        return out
    index = pd.DatetimeIndex(candles.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    s_index = pd.DatetimeIndex(series.index)
    if s_index.tz is None:
        s_index = s_index.tz_localize("UTC")
    left = pd.DataFrame({"t": index.tz_convert("UTC").as_unit("ns")})
    right = pd.DataFrame({"t": s_index.tz_convert("UTC").as_unit("ns"), column: series.to_numpy(dtype=float)}).sort_values("t")
    merged = pd.merge_asof(left, right, on="t", direction="backward", tolerance=max_age)
    out[column] = merged[column].to_numpy()
    return out


async def enrich_candles(
    candles: pd.DataFrame, symbol: str, timeframe: str, required: tuple[str, ...],
    start: dt.datetime | None = None, end: dt.datetime | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Agrega a las velas las columnas que pide la estrategia. Devuelve (velas, avisos)."""
    if not required or candles.empty:
        return candles, []
    idx = pd.DatetimeIndex(candles.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    start = start or idx[0].to_pydatetime()
    end = end or idx[-1].to_pydatetime() + dt.timedelta(hours=1)
    notes: list[str] = []
    # un colchon antes del inicio: la primera vela necesita el ultimo dato previo a su apertura
    pad_start = start - dt.timedelta(days=2)

    if "funding" in required:
        funding = await get_funding_rates(symbol, pad_start, end)
        if funding.empty:
            notes.append("No se pudo obtener el funding histórico: la estrategia no tendrá señales.")
        candles = attach_series(candles, funding, "funding_rate", FUNDING_MAX_AGE)
    if "open_interest" in required:
        interval = OI_INTERVAL.get(timeframe, "1h")
        oi = await get_open_interest(symbol, interval, pad_start, end)
        if oi.empty:
            notes.append("No se pudo obtener el open interest histórico: la estrategia no tendrá señales.")
        candles = attach_series(candles, oi, "open_interest", 2 * _OI_DELTA[interval])
    return candles, notes
