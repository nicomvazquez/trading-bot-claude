"""Cierre de una posicion: enviar la orden, esperar el resultado real de Bybit (PnL, comisiones, funding) y
dejarlo grabado en el Trade. Es el UNICO lugar que hace esto, para que el runner (cierre por senal, flip) y el
boton de emergencia "cerrar todo" registren exactamente lo mismo."""

import asyncio
import datetime as dt
import logging

from sqlalchemy import select

from app.db.base import async_session
from app.db.models import Trade
from app.exchange.bybit_client import bybit_client
from app.live.events import log_event
from app.live.rules import ClosedPnl, closing_side, summarize_closed_pnl, summarize_trade_costs

logger = logging.getLogger(__name__)

_COST_WINDOW = dt.timedelta(minutes=5)  # margen alrededor de opened_at/closed_at: las ordenes tardan unos
# segundos en confirmarse (ver los sleep() al abrir y cerrar), asi que el fill real puede ser un poco anterior
# o posterior a lo que quedo grabado. El funding solo ocurre a horarios fijos (cada 8h), asi que un margen
# de minutos no puede sumar de mas un evento que no correspondia.


async def _fetch_costs(symbol: str, opened_at: dt.datetime, closed_at: dt.datetime):
    try:
        executions = await bybit_client.get_executions_between(symbol, opened_at - _COST_WINDOW, closed_at + _COST_WINDOW)
        return summarize_trade_costs(executions)
    except Exception:  # noqa: BLE001 - las comisiones/funding son informativas: si fallan, el cierre no se aborta
        logger.exception("No se pudieron obtener las comisiones/funding reales del cierre")
        return None


async def record_trade_close(trade: Trade, reason: str, closed_pnl: ClosedPnl | None) -> Trade:
    """Deja en la base el resultado real del cierre. `closed_pnl` ya viene resuelto (desde `close_trade` tras
    enviar la orden, o desde la reconciliacion cuando el exchange cerro la posicion solo)."""
    now = dt.datetime.now(dt.timezone.utc)
    costs = await _fetch_costs(trade.symbol, trade.opened_at, closed_pnl.updated_time if closed_pnl else now)
    async with async_session() as session:
        db_trade = await session.get(Trade, trade.id)
        db_trade.exit_price = closed_pnl.avg_exit_price if closed_pnl else db_trade.entry_price
        db_trade.pnl = closed_pnl.closed_pnl if closed_pnl else 0.0
        db_trade.fees = costs.fees if costs else None
        db_trade.funding = costs.funding if costs else None
        db_trade.closed_at = closed_pnl.updated_time if closed_pnl else now
        db_trade.exit_reason = reason
        await session.commit()
        await session.refresh(db_trade)
        return db_trade


async def close_trade(trade: Trade, reason: str) -> bool:
    """Envia la orden reduce-only que cierra `trade` y graba el resultado real. Devuelve True si la orden se
    envio con exito. El llamador NUNCA debe asumir que la posicion esta cerrada, ni abrir una nueva en el
    mismo simbolo, sin comprobar este resultado."""
    try:
        await bybit_client.place_market_order(trade.symbol, closing_side(trade.side), trade.qty, reduce_only=True)
    except Exception as exc:
        logger.exception("Trade %s: fallo al enviar la orden de cierre", trade.id)
        await log_event(trade.strategy_instance_id, "error", f"No se pudo cerrar la posición ({reason}): {exc}")
        return False

    closed_pnl = None
    for _ in range(4):  # Bybit tarda un instante en publicar el cierre
        await asyncio.sleep(1.0)
        closed_pnl = summarize_closed_pnl(await bybit_client.get_closed_pnl_records(trade.symbol), trade.opened_at)
        if closed_pnl is not None:
            break

    updated = await record_trade_close(trade, reason, closed_pnl)
    logger.info("Trade %s: cerrado (%s)", trade.id, reason)
    pnl_text = f", PnL {updated.pnl:+.4f} USD" if updated.pnl is not None else ", PnL no disponible aún"
    fees_text = f", comisiones {updated.fees:.4f}" if updated.fees is not None else ""
    await log_event(trade.strategy_instance_id, "closed", f"Cerrada por {reason}{pnl_text}{fees_text}")
    return True


async def load_open_trades() -> list[Trade]:
    """TODAS las posiciones abiertas, de cualquier instancia (corriendo o no). Separada de close_many para que
    un test pueda cerrar una lista puntual de trades sin correr el riesgo de tocar, sin querer, cualquier otra
    posicion real que hubiera en la base en ese momento (ver el incidente documentado en close_many)."""
    async with async_session() as session:
        return list((await session.execute(select(Trade).where(Trade.closed_at.is_(None)))).scalars())


async def close_many(trades: list[Trade]) -> list[dict]:
    """Cierra exactamente los trades de la lista recibida, uno por uno, y devuelve un resultado por cada uno.

    ADVERTENCIA para tests: esta funcion envia ordenes reales (o al fake que este parcheado) para CADA trade
    de la lista. Nunca la llames con trades que no creaste vos mismo en el test: en una base compartida con
    datos reales (como la que usan los tests de este proyecto, que corren contra Postgres real dentro de
    Docker) close_all_positions() lee TODO lo que este abierto, incluidas posiciones reales de otras
    instancias -- probarla directo corrompio, en desarrollo, el registro de una posicion real que seguia
    abierta y para colmo el runner de otra instancia activa la "adopto" al verla huerfana. Para testear el
    boton de emergencia, armá la lista de trades vos mismo y pasala aca."""
    results = []
    for trade in trades:
        ok = await close_trade(trade, "cierre_manual")
        results.append({"trade_id": trade.id, "symbol": trade.symbol, "side": trade.side, "qty": trade.qty, "ok": ok})
    return results


async def close_all_positions() -> list[dict]:
    """Boton de emergencia: cierra TODAS las posiciones abiertas de TODAS las instancias, esten corriendo o
    no (por ejemplo, una posicion que quedo huerfana de una instancia ya apagada). No apaga las instancias ni
    activa el kill-switch por si solo: solo saca al bot del mercado ya. Devuelve un resultado por posicion."""
    return await close_many(await load_open_trades())
