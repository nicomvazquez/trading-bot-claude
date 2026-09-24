"""Alta, edicion, duplicado y borrado de instancias de estrategia, y el chequeo
previo de tamano de posicion que se muestra antes de guardarlas."""

import math
from dataclasses import dataclass

from sqlalchemy import func, select

from app.config import settings
from app.db.base import async_session
from app.db.models import BotSettings, Order, StrategyEvent, StrategyInstance, Trade
from app.exchange.bybit_client import bybit_client
from app.live.orchestrator import orchestrator
from app.live.rules import conflict_message, find_symbol_conflict
from app.risk.sizing import position_size
from app.strategies import registry


class InstanceError(ValueError):
    """Error de validacion que se puede mostrar tal cual al usuario."""


# ------------------------------------------------------------------ tamano

@dataclass
class SizingPreview:
    ok: bool
    qty: float
    notional: float
    risk_amount: float
    min_qty: float
    min_capital: float  # capital minimo para que la posicion alcance el minimo del simbolo
    message: str


def evaluate_sizing(
    capital: float, price: float, risk_pct: float, stop_loss_pct: float,
    max_leverage: float, min_qty: float, qty_step: float,
) -> SizingPreview:
    """Simula el tamano de una entrada con el mismo calculo que usa la operativa en
    vivo (risk_based topeado por apalancamiento) y lo compara con el minimo del simbolo."""
    stop = price * (1 - stop_loss_pct / 100)
    raw = position_size(capital, price, stop, risk_pct, max_leverage)
    qty = math.floor(raw / qty_step + 1e-9) * qty_step if qty_step > 0 else raw
    notional = qty * price
    risk_amount = capital * risk_pct / 100
    # capital necesario: por riesgo (qty = capital*risk / (price*stop%)) y por tope de apalancamiento
    by_risk = min_qty * price * stop_loss_pct / risk_pct if risk_pct > 0 else float("inf")
    by_leverage = min_qty * price / max_leverage if max_leverage > 0 else float("inf")
    min_capital = max(by_risk, by_leverage)
    ok = qty >= min_qty
    if ok:
        message = (
            f"Cada entrada sería de ~{qty:g} (≈ ${notional:,.0f}), arriesgando ${risk_amount:,.2f} hasta el stop."
        )
    else:
        message = (
            f"Con este capital la posición sería de ~${raw * price:,.0f}, por debajo del mínimo del símbolo "
            f"({min_qty:g} ≈ ${min_qty * price:,.0f}): la instancia no podría operar. "
            f"Necesitás al menos ~${math.ceil(min_capital):,} de capital con este riesgo y stop, o subir el % de riesgo."
        )
    return SizingPreview(ok, qty, notional, risk_amount, min_qty, min_capital, message)


async def sizing_preview(symbol: str, capital: float, params: dict) -> SizingPreview | None:
    """None si la estrategia no tiene un stop porcentual fijo (el tamano depende de cada senal).
    Lanza InstanceError si el simbolo no existe en Bybit."""
    risk_pct, stop_pct = params.get("risk_pct"), params.get("stop_loss_pct")
    if not risk_pct or not stop_pct:
        return None
    try:
        info = await bybit_client.get_instrument_info(symbol)
        rows = await bybit_client.get_klines(symbol, "1", limit=1)
    except Exception as exc:  # noqa: BLE001
        raise InstanceError(f"No se pudo consultar {symbol} en Bybit: {exc}") from exc
    if not rows:
        raise InstanceError(f"Bybit no devolvió precios para {symbol}.")
    price = float(rows[0][4])
    async with async_session() as session:
        settings_row = await session.get(BotSettings, 1)
    leverage = settings_row.max_leverage if settings_row else 10.0
    return evaluate_sizing(capital, price, risk_pct, stop_pct, leverage, info["min_qty"], info["qty_step"])


# ------------------------------------------------------------------ CRUD

def _validate(strategy_key: str, name: str, symbol: str, capital: float, params: dict) -> dict:
    name, symbol = (name or "").strip(), (symbol or "").strip().upper()
    if not name:
        raise InstanceError("Ponele un nombre a la instancia.")
    if not symbol:
        raise InstanceError("Indicá el símbolo (por ejemplo BTCUSDT).")
    if not capital or capital <= 0:
        raise InstanceError("El capital debe ser mayor a cero.")
    strategy_cls = registry.get(strategy_key)
    try:
        return strategy_cls.params_model(**params).model_dump()
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError
        raise InstanceError(f"Parámetros inválidos: {exc}") from exc


async def _ensure_unique_name(name: str, exclude_id: int | None = None) -> None:
    async with async_session() as session:
        query = select(StrategyInstance.id).where(StrategyInstance.name == name.strip())
        existing = (await session.execute(query)).scalars().first()
    if existing is not None and existing != exclude_id:
        raise InstanceError(f"Ya existe una instancia llamada «{name.strip()}».")


async def create_instance(strategy_key: str, name: str, symbol: str, timeframe: str, capital: float, params: dict) -> int:
    params = _validate(strategy_key, name, symbol, capital, params)
    await _ensure_unique_name(name)
    async with async_session() as session:
        instance = StrategyInstance(
            name=name.strip(), strategy_key=strategy_key, symbol=symbol.strip().upper(), timeframe=timeframe,
            params=params, initial_capital=capital, is_active=False,
        )
        session.add(instance)
        await session.commit()
        return instance.id


async def update_instance(instance_id: int, name: str, symbol: str, timeframe: str, capital: float, params: dict) -> None:
    """Guarda los cambios. Si la instancia esta corriendo se reinicia para que tome los nuevos valores
    (una posicion abierta se conserva: el runner la reconcilia contra Bybit al arrancar)."""
    async with async_session() as session:
        instance = await session.get(StrategyInstance, instance_id)
        if instance is None:
            raise InstanceError("La instancia ya no existe.")
        strategy_key = instance.strategy_key
    params = _validate(strategy_key, name, symbol, capital, params)
    await _ensure_unique_name(name, exclude_id=instance_id)
    async with async_session() as session:
        current = await session.get(StrategyInstance, instance_id)
        if current.is_active and current.symbol != symbol.strip().upper():
            await _check_symbol_free(instance_id, symbol)  # cambiar el simbolo de una instancia activa tambien cuenta
    async with async_session() as session:
        instance = await session.get(StrategyInstance, instance_id)
        instance.name, instance.symbol, instance.timeframe = name.strip(), symbol.strip().upper(), timeframe
        instance.initial_capital, instance.params = capital, params
        await session.commit()
    if orchestrator.is_running(instance_id):
        await orchestrator.deactivate(instance_id)
        orchestrator.activate(instance_id)


async def duplicate_instance(instance_id: int) -> int:
    async with async_session() as session:
        src = await session.get(StrategyInstance, instance_id)
        if src is None:
            raise InstanceError("La instancia ya no existe.")
        base, n = src.strategy_key, 1
        taken = set((await session.execute(select(StrategyInstance.name))).scalars())
        while f"{base}-{n}" in taken:
            n += 1
        copy = StrategyInstance(
            name=f"{base}-{n}", strategy_key=src.strategy_key, symbol=src.symbol, timeframe=src.timeframe,
            params=dict(src.params), initial_capital=src.initial_capital, is_active=False,
        )
        session.add(copy)
        await session.commit()
        return copy.id


async def _check_symbol_free(instance_id: int, symbol: str) -> None:
    async with async_session() as session:
        others = list((await session.execute(select(StrategyInstance))).scalars())
    other = find_symbol_conflict(others, symbol, exclude_id=instance_id)
    if other is not None:
        raise InstanceError(conflict_message(other.name, symbol.strip().upper()))


async def set_active(instance_id: int, active: bool) -> None:
    if active and not settings.live_enabled:
        raise InstanceError("Esta es la copia de demostración: la operativa en vivo está desactivada.")
    async with async_session() as session:
        instance = await session.get(StrategyInstance, instance_id)
        if instance is None:
            raise InstanceError("La instancia ya no existe.")
        symbol = instance.symbol
    if active:
        await _check_symbol_free(instance_id, symbol)  # una sola instancia activa por simbolo
    async with async_session() as session:
        instance = await session.get(StrategyInstance, instance_id)
        instance.is_active = active
        await session.commit()
    if active:
        orchestrator.activate(instance_id)
    else:
        await orchestrator.deactivate(instance_id)


async def can_delete(instance_id: int) -> tuple[bool, str]:
    async with async_session() as session:
        trades = (await session.execute(
            select(func.count()).select_from(Trade).where(Trade.strategy_instance_id == instance_id)
        )).scalar_one()
    if orchestrator.is_running(instance_id):
        return False, "Apagala antes de eliminarla."
    if trades:
        return False, f"Tiene {trades} trades en el historial: no se elimina para no perder ese registro."
    return True, ""


async def delete_instance(instance_id: int) -> None:
    ok, reason = await can_delete(instance_id)
    if not ok:
        raise InstanceError(reason)
    async with async_session() as session:
        for model in (StrategyEvent, Order):
            for row in (await session.execute(select(model).where(model.strategy_instance_id == instance_id))).scalars():
                await session.delete(row)
        instance = await session.get(StrategyInstance, instance_id)
        if instance is not None:
            await session.delete(instance)
        await session.commit()
