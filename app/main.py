import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from nicegui import ui

from app.config import settings
from app.db.base import init_db
from app.exchange.bybit_client import bybit_client
from app.live import environment
from app.live.alerts import send_alert
from app.live.orchestrator import orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def check_security() -> None:
    """La base de datos no debe quedar con la contrasena por defecto. En mainnet es un error fatal.
    Se llama despues de sync_from_db para reflejar el entorno REALMENTE activo, no solo el del .env
    (BotSettings pudo haber quedado en mainnet de un reinicio anterior)."""
    if settings.db_password in ("changeme", ""):
        message = "DB_PASSWORD es la de por defecto: cambiala en .env (y en Postgres con ALTER USER)."
        if not bybit_client.is_demo:
            raise RuntimeError(message + " En MAINNET no se puede iniciar así.")
        logging.getLogger(__name__).warning(message)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await environment.sync_from_db()
    check_security()
    if settings.live_enabled:
        await orchestrator.start_all_active()
    else:
        logging.getLogger(__name__).warning("MODO DEMOSTRACION: la operativa en vivo esta desactivada (LIVE_ENABLED=false)")
    yield
    await orchestrator.shutdown()
    # Aviso de mejor esfuerzo: solo cubre un apagado ordenado (docker compose stop/restart), no una caida
    # abrupta del proceso (un crash o un kill -9 no llegan a correr este codigo).
    if settings.live_enabled:
        await send_alert("🔌 El bot se detuvo (apagado ordenado). Las posiciones abiertas siguen protegidas por su stop-loss en Bybit.")


app = FastAPI(title="Bot Trading - Bybit Futures", lifespan=lifespan)

# Registra las paginas (cada modulo llama a @ui.page al importarse).
from app.ui.pages import ayuda, backtesting, configuracion, estrategias, operaciones, overview  # noqa: E402,F401

ui.run_with(app, title="Bot Trading", language="es")

if __name__ in {"__main__", "__mp_main__"}:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.app_port)
