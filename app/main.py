import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from nicegui import app as nicegui_app, ui

from app.auth import AuthMiddleware
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
    """Chequeos que no deben pasar de largo. El login (APP_PASSWORD_HASH/APP_STORAGE_SECRET) es
    obligatorio siempre, no solo en mainnet: sin login, el dashboard controla el bot (kill-switch,
    encender instancias, cambiar de entorno) sin ninguna barrera. DB_PASSWORD por defecto es fatal
    solo en mainnet porque en demo no hay dinero real en juego del lado de Bybit, pero la base sigue
    valiendo la pena proteger; se llama despues de sync_from_db para ver el entorno REALMENTE activo."""
    if not settings.app_password_hash or not settings.app_storage_secret:
        raise RuntimeError(
            "Falta configurar el login del dashboard: generá APP_PASSWORD_HASH con "
            "`python -m app.tools.set_password` y APP_STORAGE_SECRET con "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"`, y completá los dos en .env."
        )
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


@app.get("/health")
async def health() -> dict:
    """Usado por el healthcheck de Docker: confirma que el proceso responde y que puede hablar con la base.
    Si la base no responde, FastAPI devuelve 500 y el contenedor se marca unhealthy."""
    from sqlalchemy import text

    from app.db.base import engine

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}


# Registra las paginas (cada modulo llama a @ui.page al importarse).
from app.ui.pages import ayuda, backtesting, configuracion, estrategias, login, operaciones, overview  # noqa: E402,F401

# Debe agregarse ANTES de ui.run_with: Starlette apila el middleware agregado despues por encima
# del nuestro, y necesitamos que la sesion (que arma ui.run_with via storage_secret) ya este lista
# cuando el nuestro se ejecuta.
nicegui_app.add_middleware(AuthMiddleware)

ui.run_with(app, title="Bot Trading", language="es", storage_secret=settings.app_storage_secret)

if __name__ in {"__main__", "__mp_main__"}:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.app_port)
