from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


# Todavia no usamos Alembic: para columnas nuevas en tablas ya existentes,
# create_all no alcanza (solo crea tablas faltantes), asi que las parcheamos
# a mano con ADD COLUMN IF NOT EXISTS, que es idempotente y no toca datos.
_SCHEMA_PATCHES = [
    "ALTER TABLE strategy_instances ADD COLUMN IF NOT EXISTS initial_capital FLOAT DEFAULT 100.0",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_loss FLOAT",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS take_profit FLOAT",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason VARCHAR",
]


async def init_db() -> None:
    from app.db import models  # noqa: F401 registers tables on Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for patch in _SCHEMA_PATCHES:
            await conn.execute(text(patch))
