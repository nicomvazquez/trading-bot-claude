"""Bitacora de eventos por instancia (ver StrategyEvent). Los de tipo "error" tambien se mandan por
Telegram (ver app.live.alerts): son senales/ordenes que fallaron de verdad, algo que amerita mirar ya."""

import logging

from sqlalchemy import select

from app.db.base import async_session
from app.db.models import StrategyEvent, StrategyInstance
from app.live.alerts import send_alert

logger = logging.getLogger(__name__)


async def log_event(instance_id: int, kind: str, message: str) -> None:
    """Registra un evento. Si es identico al ultimo de la instancia no se repite:
    un error persistente en el loop de 20 s llenaria la tabla (y mandaria la misma alerta sin parar)."""
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
            instance_name = None
            if kind == "error":
                instance = await session.get(StrategyInstance, instance_id)
                instance_name = instance.name if instance else None
    except Exception:  # noqa: BLE001 - la bitacora nunca debe frenar la operatoria
        logger.exception("No se pudo registrar el evento de la instancia %s", instance_id)
        return

    if kind == "error":
        await send_alert(f"⚠️ {instance_name or f'Instancia {instance_id}'}: {message}")


async def load_events(instance_id: int, limit: int = 30) -> list[StrategyEvent]:
    async with async_session() as session:
        result = await session.execute(
            select(StrategyEvent).where(StrategyEvent.strategy_instance_id == instance_id)
            .order_by(StrategyEvent.id.desc()).limit(limit)
        )
        return list(result.scalars())
