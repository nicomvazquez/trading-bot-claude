import datetime as dt

import pandas as pd
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.backtest.quality import DataQualityReport, assess_candles, bar_delta, sanitize_candles
from app.db.base import async_session
from app.db.models import Candle
from app.exchange.bybit_client import bybit_client

_INSERT_BATCH = 2500  # asyncpg admite como maximo 32767 parametros por sentencia


def _is_suspect(td: dt.timedelta):
    """Vela guardada sin fecha de descarga, o descargada antes de cerrar."""
    return or_(Candle.fetched_at.is_(None), Candle.fetched_at < Candle.timestamp + td)


async def _store_candles(symbol: str, timeframe: str, candles: list[dict], td: dt.timedelta) -> None:
    """Guarda solo velas CERRADAS y pisa las existentes: una vela guardada
    incompleta se corrige al volver a descargarla."""
    now = dt.datetime.now(dt.timezone.utc)
    rows = [
        {"symbol": symbol, "timeframe": timeframe, **c, "fetched_at": now}
        for c in candles
        if c["timestamp"] + td <= now
    ]
    if not rows:
        return
    async with async_session() as session:
        for i in range(0, len(rows), _INSERT_BATCH):
            stmt = pg_insert(Candle).values(rows[i : i + _INSERT_BATCH])
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "timeframe", "timestamp"],
                set_={
                    "open": stmt.excluded.open, "high": stmt.excluded.high, "low": stmt.excluded.low,
                    "close": stmt.excluded.close, "volume": stmt.excluded.volume,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            await session.execute(stmt)
        await session.commit()


async def _plan_fetch(symbol: str, timeframe: str, start: dt.datetime, end: dt.datetime, td: dt.timedelta) -> dt.datetime | None:
    """Desde donde hay que pedirle a Bybit (o None si el cache alcanza)."""
    async with async_session() as session:
        min_ts, max_ts, count = (
            await session.execute(
                select(func.min(Candle.timestamp), func.max(Candle.timestamp), func.count()).where(
                    Candle.symbol == symbol, Candle.timeframe == timeframe,
                    Candle.timestamp >= start, Candle.timestamp <= end,
                )
            )
        ).one()
        if not count:
            return start
        earliest_suspect = (
            await session.execute(
                select(func.min(Candle.timestamp)).where(
                    Candle.symbol == symbol, Candle.timeframe == timeframe,
                    Candle.timestamp >= start, Candle.timestamp <= end, _is_suspect(td),
                )
            )
        ).scalar_one()

    if min_ts > start + td:
        return start  # el cache no llega hasta el inicio pedido
    candidates = []
    if earliest_suspect is not None:
        candidates.append(earliest_suspect)
    if max_ts < end - 2 * td:
        candidates.append(max_ts)  # falta la cola reciente
    return min(candidates) if candidates else None


async def load_candles(
    symbol: str, timeframe: str, start: dt.datetime, end: dt.datetime
) -> tuple[pd.DataFrame, DataQualityReport]:
    """Velas OHLCV cerradas en [start, end] (index=timestamp, UTC) + reporte
    de calidad. Usa la base como cache y le pide a Bybit solo lo que falta o
    lo que se guardo incompleto."""
    td = bar_delta(timeframe).to_pytimedelta()
    fetch_from = await _plan_fetch(symbol, timeframe, start, end, td)
    if fetch_from is not None:
        fetched = await bybit_client.get_historical_klines(symbol, timeframe, fetch_from, end)
        await _store_candles(symbol, timeframe, fetched, td)

    now = dt.datetime.now(dt.timezone.utc)
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Candle.timestamp, Candle.open, Candle.high, Candle.low, Candle.close, Candle.volume)
                .where(
                    Candle.symbol == symbol, Candle.timeframe == timeframe,
                    Candle.timestamp >= start, Candle.timestamp <= end,
                    Candle.timestamp + td <= now,  # nunca una vela que todavia se esta formando
                )
                .order_by(Candle.timestamp)
            )
        ).all()

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if df.empty:
        return df, assess_candles(df, timeframe)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp")
    df, invalid, duplicates = sanitize_candles(df)
    return df, assess_candles(df, timeframe, invalid, duplicates)


async def get_candles(symbol: str, timeframe: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    df, _ = await load_candles(symbol, timeframe, start, end)
    return df
