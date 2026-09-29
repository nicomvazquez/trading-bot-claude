"""Crea el esquema una vez por sesion de tests, si hay una Postgres alcanzable (siempre en CI; en el
host de Windows no, asi que los tests que la necesitan siguen salteandose solos via su propio chequeo)."""

import asyncio

import pytest

from app.db.base import engine, init_db


@pytest.fixture(scope="session", autouse=True)
def _init_schema_if_db_available():
    async def _try() -> None:
        try:
            await init_db()
        except Exception:  # noqa: BLE001 - sin DB alcanzable: los tests que la necesitan se saltean solos
            pass
        finally:
            await engine.dispose()

    asyncio.run(_try())
