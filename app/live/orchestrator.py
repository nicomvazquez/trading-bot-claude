import logging

from sqlalchemy import select

from app.db.base import async_session
from app.db.models import StrategyInstance
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
            result = await session.execute(select(StrategyInstance).where(StrategyInstance.is_active.is_(True)))
            for instance in result.scalars().all():
                self.activate(instance.id)

    def activate(self, instance_id: int) -> None:
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
