"""Consultas a la base para las paginas de operativa en vivo."""

from sqlalchemy import select

from app.db.base import async_session
from app.db.models import Order, StrategyInstance, Trade


async def load_instances() -> list[StrategyInstance]:
    async with async_session() as session:
        return list((await session.execute(select(StrategyInstance).order_by(StrategyInstance.id))).scalars())


async def load_trades() -> list[Trade]:
    async with async_session() as session:
        return list((await session.execute(select(Trade).order_by(Trade.opened_at.desc()))).scalars())


async def load_orders(limit: int = 200) -> list[Order]:
    async with async_session() as session:
        return list((await session.execute(select(Order).order_by(Order.created_at.desc()).limit(limit))).scalars())


async def load_bot_status() -> dict:
    """Lo minimo que muestra el menu lateral en todas las paginas."""
    from app.db.models import BotSettings
    from app.live.orchestrator import orchestrator

    async with async_session() as session:
        settings_row = await session.get(BotSettings, 1)
        instances = (await session.execute(select(StrategyInstance))).scalars().all()
    return {
        "kill_switch": bool(settings_row and settings_row.kill_switch),
        "running": sum(1 for i in instances if orchestrator.is_running(i.id)),
        "total": len(instances),
    }
