import datetime as dt

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import async_session
from app.db.models import FundingRate
from app.exchange.bybit_client import bybit_client


async def get_funding_rates(symbol: str, start: dt.datetime, end: dt.datetime) -> pd.Series:
    """Funding historico real de Bybit como fraccion (0.0001 = 0.01%), indexado
    por el instante de cobro. Positivo: los largos pagan a los cortos."""
    async with async_session() as session:
        min_ts, max_ts, count = (
            await session.execute(
                select(func.min(FundingRate.timestamp), func.max(FundingRate.timestamp), func.count()).where(
                    FundingRate.symbol == symbol, FundingRate.timestamp >= start, FundingRate.timestamp <= end
                )
            )
        ).one()

    slack = dt.timedelta(hours=9)  # el intervalo de funding mas comun es de 8h
    if not count or min_ts > start + slack or max_ts < end - slack:
        fetched = await bybit_client.get_funding_history(symbol, start, end)
        if fetched:
            async with async_session() as session:
                stmt = pg_insert(FundingRate).values(
                    [{"symbol": symbol, "timestamp": f["timestamp"], "rate": f["rate"]} for f in fetched]
                )
                await session.execute(stmt.on_conflict_do_nothing(index_elements=["symbol", "timestamp"]))
                await session.commit()

    async with async_session() as session:
        rows = (
            await session.execute(
                select(FundingRate.timestamp, FundingRate.rate)
                .where(FundingRate.symbol == symbol, FundingRate.timestamp >= start, FundingRate.timestamp <= end)
                .order_by(FundingRate.timestamp)
            )
        ).all()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        [r[1] for r in rows], index=pd.DatetimeIndex([r[0] for r in rows], tz="UTC"), dtype=float
    )
