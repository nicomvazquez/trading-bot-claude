"""Bitacora de eventos por instancia (ver StrategyEvent)."""

import logging

from sqlalchemy import select

from app.db.base import async_session
from app.db.models import StrategyEvent

logger = logging.getLogger(__name__)


async def log_event(instance_id: int, kind: str, message: str) -> None:
    """Registra un evento. Si es identico al ultimo de la instancia no se repite:
    un error persistente en el loop de 20 s llenaria la tabla."""
    try:
        async with async_session() as session:
            last = (
                await session.execute(
                    select(StrategyEvent).where(StrategyEvent.strategy_instance_id == instance_id)
                    .order_by(StrategyEvent.id.desc()).limit(1)
                )
            ).scalars().first()
            if last is not None and last.kind == kind and last.message == message:
                return
            session.add(StrategyEvent(strategy_instance_id=instance_id, kind=kind, message=message[:500]))
            await session.commit()
    except Exception:  # noqa: BLE001 - la bitacora nunca debe frenar la operatoria
        logger.exception("No se pudo registrar el evento de la instancia %s", instance_id)


async def load_events(instance_id: int, limit: int = 30) -> list[StrategyEvent]:
    async with async_session() as session:
        result = await session.execute(
            select(StrategyEvent).where(StrategyEvent.strategy_instance_id == instance_id)
            .order_by(StrategyEvent.id.desc()).limit(limit)
        )
        return list(result.scalars())
