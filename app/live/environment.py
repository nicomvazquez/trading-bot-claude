"""Cambio de entorno de trading (demo <-> mainnet) desde el dashboard, sin editar el archivo
.env ni reiniciar el proceso.

bybit_client.configure() reconstruye el cliente HTTP con las credenciales del entorno elegido;
BotSettings.bybit_env persiste esa eleccion para que sobreviva un reinicio (sync_from_db la
vuelve a aplicar al arrancar, porque el cliente siempre arranca en el entorno del .env).

Por la incidencia documentada en app.live.closing.close_many: cambiar de entorno con alguna
instancia corriendo o con posiciones abiertas registradas en la base dejaria trades que ya no
corresponden a ninguna posicion real de la cuenta que queda activa (o, peor, coinciden por
simbolo con una posicion real de la OTRA cuenta). Por eso switch_to exige, antes de tocar nada,
que no haya ninguna instancia corriendo y ninguna posicion abierta en la base."""

import logging

from app.config import settings
from app.db.base import async_session
from app.db.models import BotSettings
from app.exchange.bybit_client import bybit_client
from app.live.closing import load_open_trades
from app.live.orchestrator import orchestrator

logger = logging.getLogger(__name__)


class EnvironmentError(ValueError):
    """Error de validacion que se puede mostrar tal cual al usuario."""


def mainnet_configured() -> bool:
    return bool(settings.bybit_mainnet_api_key and settings.bybit_mainnet_api_secret)


async def can_switch_to(target: str) -> tuple[bool, str]:
    if target not in ("demo", "mainnet"):
        return False, "Entorno inválido."
    if target == "mainnet" and not mainnet_configured():
        return False, (
            "Faltan las credenciales de mainnet: completá BYBIT_MAINNET_API_KEY y "
            "BYBIT_MAINNET_API_SECRET en el archivo .env y reiniciá la app."
        )
    if target == "mainnet" and settings.db_password in ("changeme", ""):
        return False, "DB_PASSWORD sigue siendo la de por defecto: cambiala antes de operar en mainnet."
    if orchestrator.running_count() > 0:
        return False, "Apagá todas las instancias antes de cambiar de entorno."
    if await load_open_trades():
        return False, (
            "Hay posiciones abiertas registradas en la base: cerralas (podés usar «Cerrar todo» en "
            "esta misma página) antes de cambiar de entorno."
        )
    return True, ""


async def switch_to(target: str) -> None:
    ok, reason = await can_switch_to(target)
    if not ok:
        raise EnvironmentError(reason)
    await bybit_client.configure(demo=(target != "mainnet"))
    async with async_session() as session:
        row = await session.get(BotSettings, 1)
        if row is None:
            row = BotSettings(id=1)
            session.add(row)
        row.bybit_env = target
        await session.commit()
    logger.warning("Entorno de trading cambiado a %s", target)


async def sync_from_db() -> None:
    """Al arrancar: aplica al cliente el entorno que habia quedado elegido en el dashboard la
    ultima vez. bybit_client siempre arranca leyendo el .env (ver BybitClient.__init__, que corre
    antes de que exista un event loop), asi que si BotSettings dice algo distinto hay que
    resincronizarlo aca, ya con la base disponible."""
    async with async_session() as session:
        row = await session.get(BotSettings, 1)
    target = row.bybit_env if row else "demo"
    if target == "mainnet" and not mainnet_configured():
        logger.warning("BotSettings quedo en mainnet pero faltan las credenciales en .env: se arranca en demo.")
        return
    demo = target != "mainnet"
    if demo != bybit_client.is_demo:
        await bybit_client.configure(demo=demo)
        logger.warning("Entorno de trading resincronizado a %s (elegido antes del reinicio)", target)
