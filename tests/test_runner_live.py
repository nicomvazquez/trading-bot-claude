"""Tests de integracion de StrategyRunner contra la base de datos real, con un exchange falso en vez de Bybit.

No corren en el host de Windows (DB_HOST=db solo resuelve dentro de la red de Docker): se saltean solos si
la base no esta disponible. Para correrlos de verdad:

    docker compose exec app python -m pytest tests/test_runner_live.py -q

Cada test crea su propia instancia de estrategia (simbolo TESTUSDT-<random>, nunca usado por instancias
reales) y la borra al terminar, incluso si el test falla."""

import asyncio
import secrets

import pandas as pd
import pytest
from sqlalchemy import delete, select, text

from app.db.base import async_session, engine
from app.db.models import Order, StrategyEvent, StrategyInstance, Trade
from app.live import runner as runner_module
from app.live.runner import StrategyRunner
from app.strategies.base import Signal

CANDLES = pd.DataFrame({"open": [100.0], "high": [100.0], "low": [100.0], "close": [100.0], "volume": [1.0]})


async def _db_available() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def run_async(coro_fn):
    """Decorador chico: define el test como sync (lo que pytest recolecta sin plugins) y corre el cuerpo
    async con asyncio.run(), salteando primero si la base no esta disponible."""

    def wrapper():
        async def body():
            if not await _db_available():
                pytest.skip("requiere DB_HOST alcanzable (correr con: docker compose exec app python -m pytest tests/test_runner_live.py)")
            try:
                await coro_fn()
            finally:
                # el engine es un singleton del proceso, pero cada test corre en su propio event loop
                # (asyncio.run crea uno nuevo): sin esto, el pool de conexiones del test siguiente
                # queda atado a un loop ya cerrado y las conexiones fallan.
                await engine.dispose()

        asyncio.run(body())

    wrapper.__name__ = coro_fn.__name__
    return wrapper


class ScriptedStrategy:
    """Devuelve, en orden, una senal prefijada por cada llamada a on_candle (ninguna logica real)."""

    def __init__(self, *signals: Signal | None) -> None:
        self._signals = list(signals)

    def on_candle(self, ctx):
        return self._signals.pop(0) if self._signals else None


class FakeBybit:
    """Reemplaza a bybit_client dentro de app.live.runner para estos tests: sin red, comportamiento controlado."""

    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.fail_next_close = False
        self._position: dict | None = None

    async def round_qty(self, symbol, qty):
        return round(qty, 6)

    async def get_instrument_info(self, symbol):
        return {"min_qty": 0.001, "qty_step": 0.001, "tick_size": 0.1}

    async def place_market_order(self, symbol, side, qty, stop_loss=None, take_profit=None, reduce_only=False):
        if reduce_only and self.fail_next_close:
            self.fail_next_close = False
            raise RuntimeError("orden de cierre rechazada (simulado)")
        self.orders.append({"symbol": symbol, "side": side, "qty": qty, "reduce_only": reduce_only})
        if reduce_only:
            self._position = None
        else:
            self._position = {"side": "long" if side == "Buy" else "short", "qty": qty, "entry_price": 100.0, "unrealised_pnl": 0.0}
        return {"orderId": f"fake-{len(self.orders)}"}

    async def get_open_position(self, symbol):
        return self._position

    async def get_closed_pnl_records(self, symbol, limit=50):
        return []


async def _make_instance(symbol_suffix: str) -> StrategyInstance:
    async with async_session() as session:
        instance = StrategyInstance(
            name=f"test-runner-{symbol_suffix}", strategy_key="donchian_breakout", symbol=f"TESTUSDT{symbol_suffix}",
            timeframe="60", params={}, initial_capital=1000.0, is_active=False,
        )
        session.add(instance)
        await session.commit()
        await session.refresh(instance)
        return instance


async def _cleanup(instance_id: int) -> None:
    async with async_session() as session:
        for model in (StrategyEvent, Order, Trade):  # Order y Trade tienen FK a strategy_instances
            await session.execute(delete(model).where(model.strategy_instance_id == instance_id))
        await session.execute(delete(StrategyInstance).where(StrategyInstance.id == instance_id))
        await session.commit()


async def _open_trades(instance_id: int) -> list[Trade]:
    async with async_session() as session:
        result = await session.execute(select(Trade).where(Trade.strategy_instance_id == instance_id, Trade.closed_at.is_(None)))
        return list(result.scalars())


async def _events(instance_id: int) -> list[StrategyEvent]:
    async with async_session() as session:
        result = await session.execute(select(StrategyEvent).where(StrategyEvent.strategy_instance_id == instance_id).order_by(StrategyEvent.id))
        return list(result.scalars())


@run_async
async def test_a_failed_flip_close_never_opens_the_opposite_position():
    """El bug: si el cierre de la posicion contraria falla, el runner NO debe abrir la nueva de todos modos
    (eso dejaria dos posiciones opuestas en el exchange y un trade huerfano en la base)."""
    instance = await _make_instance(secrets.token_hex(3))
    fake = FakeBybit()
    patches = _patch_runner(fake)
    try:
        runner = StrategyRunner(instance.id)

        # 1) abre un long
        opened = ScriptedStrategy(Signal(action="buy", stop_loss=90.0, risk_pct=1.0))
        await runner._on_new_candle(instance, opened, CANDLES)
        trades = await _open_trades(instance.id)
        assert len(trades) == 1 and trades[0].side == "long"
        original_trade_id = trades[0].id
        assert len(fake.orders) == 1  # una sola orden: la apertura

        # 2) llega una senal de flip a corto, pero el cierre del long falla
        fake.fail_next_close = True
        flip = ScriptedStrategy(Signal(action="sell", stop_loss=110.0, risk_pct=1.0))
        await runner._on_new_candle(instance, flip, CANDLES)

        # el long original sigue abierto, tal cual estaba: NO se abrio ningun short
        trades = await _open_trades(instance.id)
        assert len(trades) == 1, "el flip fallido no debe dejar mas de una posicion abierta"
        assert trades[0].id == original_trade_id and trades[0].side == "long"
        assert len(fake.orders) == 1, "no se debe haber enviado una orden de apertura del lado contrario"

        events = await _events(instance.id)
        assert any(e.kind == "error" and "cerrar" in e.message.lower() for e in events)
    finally:
        _unpatch_runner(patches)
        await _cleanup(instance.id)


@run_async
async def test_a_successful_flip_closes_the_old_side_and_opens_the_new_one():
    """Complemento del test anterior: cuando el cierre SI funciona, el flip debe completarse normalmente."""
    instance = await _make_instance(secrets.token_hex(3))
    fake = FakeBybit()
    patches = _patch_runner(fake)
    try:
        runner = StrategyRunner(instance.id)
        await runner._on_new_candle(instance, ScriptedStrategy(Signal(action="buy", stop_loss=90.0, risk_pct=1.0)), CANDLES)
        long_id = (await _open_trades(instance.id))[0].id

        await runner._on_new_candle(instance, ScriptedStrategy(Signal(action="sell", stop_loss=110.0, risk_pct=1.0)), CANDLES)

        trades = await _open_trades(instance.id)
        assert len(trades) == 1 and trades[0].side == "short" and trades[0].id != long_id
        assert len(fake.orders) == 3  # abrir long, cerrar long (reduce_only), abrir short
    finally:
        _unpatch_runner(patches)
        await _cleanup(instance.id)


async def _fake_limits():
    from app.risk.manager import RiskLimits

    return RiskLimits(kill_switch=False, max_daily_loss_pct=100.0, max_concurrent_positions=999, max_leverage=10.0)


async def _zero() -> int:
    return 0


def _patch_runner(fake: "FakeBybit") -> dict:
    """Reemplaza el exchange, el sleep y los limites/conteo globales del modulo runner por versiones
    controladas, y devuelve los originales para restaurarlos con _unpatch_runner."""
    originals = {
        "bybit_client": runner_module.bybit_client,
        "sleep": asyncio.sleep,
        "_load_limits": runner_module._load_limits,
        "_global_open_positions_count": runner_module._global_open_positions_count,
    }
    runner_module.bybit_client = fake
    runner_module.asyncio.sleep = lambda _seconds: originals["sleep"](0)  # sin perder tiempo real en los reintentos
    runner_module._load_limits = lambda: _fake_limits()  # sin tocar los limites reales de la cuenta
    runner_module._global_open_positions_count = lambda: _zero()  # aislado de posiciones reales de otras instancias
    return originals


def _unpatch_runner(originals: dict) -> None:
    runner_module.bybit_client = originals["bybit_client"]
    runner_module.asyncio.sleep = originals["sleep"]
    runner_module._load_limits = originals["_load_limits"]
    runner_module._global_open_positions_count = originals["_global_open_positions_count"]
