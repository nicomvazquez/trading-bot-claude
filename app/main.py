import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from nicegui import ui

from app.config import settings
from app.db.base import init_db
from app.live.orchestrator import orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def check_security() -> None:
    """La base de datos no debe quedar con la contrasena por defecto. En mainnet es un error fatal."""
    if settings.db_password in ("changeme", ""):
        message = "DB_PASSWORD es la de por defecto: cambiala en .env (y en Postgres con ALTER USER)."
        if not settings.bybit_demo:
            raise RuntimeError(message + " En MAINNET no se puede iniciar así.")
        logging.getLogger(__name__).warning(message)


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_security()
    await init_db()
    if settings.live_enabled:
        await orchestrator.start_all_active()
    else:
        logging.getLogger(__name__).warning("MODO DEMOSTRACION: la operativa en vivo esta desactivada (LIVE_ENABLED=false)")
    yield
    await orchestrator.shutdown()


app = FastAPI(title="Bot Trading - Bybit Futures", lifespan=lifespan)

# Registra las paginas (cada modulo llama a @ui.page al importarse).
from app.ui.pages import ayuda, backtesting, configuracion, estrategias, operaciones, overview  # noqa: E402,F401

ui.run_with(app, title="Bot Trading", language="es")

if __name__ in {"__main__", "__mp_main__"}:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.app_port)
