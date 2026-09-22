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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await orchestrator.start_all_active()
    yield
    await orchestrator.shutdown()


app = FastAPI(title="Bot Trading - Bybit Futures", lifespan=lifespan)

# Registra las paginas (cada modulo llama a @ui.page al importarse).
from app.ui.pages import backtesting, configuracion, estrategias, operaciones, overview  # noqa: E402,F401

ui.run_with(app, title="Bot Trading")

if __name__ in {"__main__", "__mp_main__"}:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.app_port)
