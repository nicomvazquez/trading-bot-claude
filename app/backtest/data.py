import datetime as dt

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import async_session
from app.db.models import Candle
from app.exchange.bybit_client import bybit_client


async def _has_full_coverage(symbol: str, timeframe: str, start: dt.datetime, end: dt.datetime) -> bool:
    async with async_session() as session:
        result = await session.execute(
            select(func.min(Candle.timestamp), func.max(Candle.timestamp)).where(
                Candle.symbol == symbol, Candle.timeframe == timeframe
            )
        )
        min_ts, max_ts = result.one()
    if min_ts is None:
        return False
    return min_ts <= start and max_ts >= end


async def _store_candles(symbol: str, timeframe: str, candles: list[dict]) -> None:
    if not candles:
        return
    rows = [{"symbol": symbol, "timeframe": timeframe, **c} for c in candles]
    # asyncpg admite como maximo 32767 parametros por sentencia (8 columnas por fila)
    batch_size = 3000
    async with async_session() as session:
        for i in range(0, len(rows), batch_size):
            stmt = pg_insert(Candle).values(rows[i : i + batch_size])
            stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "timeframe", "timestamp"])
            await session.execute(stmt)
        await session.commit()


async def get_candles(
    symbol: str, timeframe: str, start: dt.datetime, end: dt.datetime
) -> pd.DataFrame:
    """Devuelve las velas OHLCV en [start, end] como DataFrame (index=timestamp),
    usando la base de datos como cache y pidiendole a Bybit solo lo que falta."""
    if not await _has_full_coverage(symbol, timeframe, start, end):
        fetched = await bybit_client.get_historical_klines(symbol, timeframe, start, end)
        await _store_candles(symbol, timeframe, fetched)

    async with async_session() as session:
        result = await session.execute(
            select(Candle)
            .where(
                Candle.symbol == symbol,
                Candle.timeframe == timeframe,
                Candle.timestamp >= start,
                Candle.timestamp <= end,
            )
            .order_by(Candle.timestamp)
        )
        rows = result.scalars().all()

    df = pd.DataFrame(
        [
            {
                "timestamp": r.timestamp,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in rows
        ]
    )
    if not df.empty:
        df = df.set_index("timestamp")
    return df
