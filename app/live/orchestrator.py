import logging

from sqlalchemy import select

from app.config import settings

from app.db.base import async_session
from app.db.models import StrategyInstance
from app.live.events import log_event
from app.live.rules import conflict_message
from app.live.runner import StrategyRunner

logger = logging.getLogger(__name__)


class Orchestrator:
    """Mantiene un StrategyRunner por cada instancia activa. Es el unico
    punto de entrada para prender/apagar ejecucion en vivo: la UI nunca
    maneja tasks de asyncio directamente."""

    def __init__(self) -> None:
        self._runners: dict[int, StrategyRunner] = {}

    async def start_all_active(self) -> None:
        async with async_session() as session:
            result = await session.execute(
                select(StrategyInstance).where(StrategyInstance.is_active.is_(True)).order_by(StrategyInstance.id)
            )
            started: dict[str, str] = {}  # simbolo -> instancia que lo opera
            for instance in result.scalars().all():
                symbol = instance.symbol.strip().upper()
                if symbol in started:
                    # datos viejos con dos instancias activas sobre el mismo simbolo: solo arranca la primera
                    instance.is_active = False
                    logger.warning("Instancia %s no arranca: %s ya lo opera %s", instance.name, symbol, started[symbol])
                    await session.commit()
                    await log_event(instance.id, "error", "No se encendió al iniciar la app. " + conflict_message(started[symbol], symbol))
                    continue
                started[symbol] = instance.name
                self.activate(instance.id)

    def activate(self, instance_id: int) -> None:
        if not settings.live_enabled:
            logger.warning("LIVE_ENABLED=false: no se activa la instancia %s", instance_id)
            return
        if instance_id in self._runners:
            return
        runner = StrategyRunner(instance_id)
        runner.start()
        self._runners[instance_id] = runner
        logger.info("Orquestador: instancia %s activada", instance_id)

    async def deactivate(self, instance_id: int) -> None:
        runner = self._runners.pop(instance_id, None)
        if runner is not None:
            await runner.stop()
            logger.info("Orquestador: instancia %s desactivada", instance_id)

    async def shutdown(self) -> None:
        for instance_id in list(self._runners.keys()):
            await self.deactivate(instance_id)

    def is_running(self, instance_id: int) -> bool:
        return instance_id in self._runners


orchestrator = Orchestrator()
