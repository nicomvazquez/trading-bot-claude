"""Tests de app.live.environment (selector de entorno demo/mainnet) contra la base real.

Nunca tocan el bybit_client real ni dejan la fila global de BotSettings distinta de como la
encontraron: es la misma fila que lee la app en vivo en cada reinicio (ver sync_from_db), asi
que el test que la escribe (switch_to) la restaura siempre en un finally, y todos los demas
prueban solo can_switch_to, que no escribe nada.

Mismo patron que tests/test_runner_live.py: se saltean solos si DB_HOST no esta accesible.
    docker compose exec app python -m pytest tests/test_environment.py -q
"""

import asyncio

import pytest
from sqlalchemy import text

from app.config import settings
from app.db.base import async_session, engine
from app.db.models import BotSettings
from app.live import environment as env_module


async def _db_available() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def run_async(coro_fn):
    def wrapper():
        async def body():
            if not await _db_available():
                pytest.skip("requiere DB_HOST alcanzable (correr con: docker compose exec app python -m pytest tests/test_environment.py)")
            try:
                await coro_fn()
            finally:
                await engine.dispose()

        asyncio.run(body())

    wrapper.__name__ = coro_fn.__name__
    return wrapper


class FakeBybitClient:
    """Nunca se usa el bybit_client real en estos tests: switch_to() llamaria a Bybit de verdad
    con las credenciales del entorno destino si no se reemplazara."""

    def __init__(self) -> None:
        self.is_demo = True
        self.configure_calls: list[bool] = []

    async def configure(self, demo: bool) -> None:
        self.configure_calls.append(demo)
        self.is_demo = demo


async def _empty_trades() -> list:
    return []


async def _one_fake_trade() -> list:
    return [object()]


async def _always_ok(target: str) -> tuple[bool, str]:
    return True, ""


def _patch_mainnet_creds(key: str, secret: str) -> tuple[str, str]:
    original = settings.bybit_mainnet_api_key, settings.bybit_mainnet_api_secret
    settings.bybit_mainnet_api_key, settings.bybit_mainnet_api_secret = key, secret
    return original


def _restore_mainnet_creds(original: tuple[str, str]) -> None:
    settings.bybit_mainnet_api_key, settings.bybit_mainnet_api_secret = original


@run_async
async def test_cannot_switch_to_mainnet_without_credentials():
    original = _patch_mainnet_creds("", "")
    try:
        ok, reason = await env_module.can_switch_to("mainnet")
        assert not ok
        assert "credenciales" in reason.lower()
    finally:
        _restore_mainnet_creds(original)


@run_async
async def test_cannot_switch_while_an_instance_is_running():
    original_creds = _patch_mainnet_creds("k", "s")
    original_running_count = env_module.orchestrator.running_count
    original_load_open = env_module.load_open_trades
    env_module.orchestrator.running_count = lambda: 1
    env_module.load_open_trades = _empty_trades
    try:
        ok, reason = await env_module.can_switch_to("mainnet")
        assert not ok
        assert "apagá" in reason.lower()
    finally:
        env_module.orchestrator.running_count = original_running_count
        env_module.load_open_trades = original_load_open
        _restore_mainnet_creds(original_creds)


@run_async
async def test_cannot_switch_with_open_trades_registered():
    original_creds = _patch_mainnet_creds("k", "s")
    original_running_count = env_module.orchestrator.running_count
    original_load_open = env_module.load_open_trades
    env_module.orchestrator.running_count = lambda: 0
    env_module.load_open_trades = _one_fake_trade
    try:
        ok, reason = await env_module.can_switch_to("mainnet")
        assert not ok
        assert "abiertas" in reason.lower()
    finally:
        env_module.orchestrator.running_count = original_running_count
        env_module.load_open_trades = original_load_open
        _restore_mainnet_creds(original_creds)


@run_async
async def test_can_switch_when_nothing_is_running_and_nothing_is_open():
    original_creds = _patch_mainnet_creds("k", "s")
    original_password = settings.db_password
    settings.db_password = "a-real-password"
    original_running_count = env_module.orchestrator.running_count
    original_load_open = env_module.load_open_trades
    env_module.orchestrator.running_count = lambda: 0
    env_module.load_open_trades = _empty_trades
    try:
        ok, reason = await env_module.can_switch_to("mainnet")
        assert ok and reason == ""
    finally:
        env_module.orchestrator.running_count = original_running_count
        env_module.load_open_trades = original_load_open
        settings.db_password = original_password
        _restore_mainnet_creds(original_creds)


@run_async
async def test_switch_to_calls_configure_and_persists_the_choice_then_restores_it():
    fake_client = FakeBybitClient()
    original_client = env_module.bybit_client
    original_can_switch = env_module.can_switch_to
    env_module.bybit_client = fake_client
    env_module.can_switch_to = _always_ok
    async with async_session() as session:
        row = await session.get(BotSettings, 1)
        original_env = row.bybit_env if row else "demo"
    try:
        await env_module.switch_to("mainnet")
        assert fake_client.configure_calls == [False]  # demo=False
        assert fake_client.is_demo is False
        async with async_session() as session:
            row = await session.get(BotSettings, 1)
            assert row.bybit_env == "mainnet"
    finally:
        env_module.bybit_client = original_client
        env_module.can_switch_to = original_can_switch
        async with async_session() as session:
            row = await session.get(BotSettings, 1)
            if row is not None:
                row.bybit_env = original_env
                await session.commit()
