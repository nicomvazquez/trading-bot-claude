import asyncio
import datetime as dt
import logging

from sqlalchemy import func, select

from app.backtest.extras import enrich_candles
from app.db.base import async_session
from app.db.models import BotSettings, Order, StrategyInstance, Trade
from app.exchange.bybit_client import bybit_client
from app.live.events import log_event
from app.timeutil import day_start_utc, fmt as fmt_time, label as tz_label
from app.live.rules import decide_candle, should_adopt_position, summarize_closed_pnl
from app.risk.manager import RiskLimits, RiskManager
from app.strategies import registry
from app.strategies.base import Position, StrategyContext

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = {
    "1": 60, "3": 180, "5": 300, "15": 900, "30": 1800,
    "60": 3600, "120": 7200, "240": 14400, "360": 21600, "720": 43200,
    "D": 86400, "W": 604800,
}
_POLL_SECONDS = 20  # cada cuanto se revisa si cerro una vela nueva o si el exchange cerro la posicion


async def _load_limits() -> RiskLimits:
    async with async_session() as session:
        row = await session.get(BotSettings, 1)
        if row is None:
            return RiskLimits()
        return RiskLimits(
            kill_switch=row.kill_switch,
            max_daily_loss_pct=row.max_daily_loss_pct,
            max_concurrent_positions=row.max_concurrent_positions,
            max_leverage=row.max_leverage,
        )


async def _global_open_positions_count() -> int:
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(Trade).where(Trade.closed_at.is_(None)))
        return result.scalar_one()


class StrategyRunner:
    """Ejecuta UNA instancia de estrategia en vivo: por cada vela cerrada
    nueva le pasa el contexto, la senal pasa por el RiskManager, y de ahi a
    una orden real en Bybit. Reconcilia el estado contra el exchange en cada
    ciclo para sobrevivir a un reinicio del proceso con una posicion abierta."""

    def __init__(self, instance_id: int) -> None:
        self.instance_id = instance_id
        self._task: asyncio.Task | None = None
        self._stop_requested = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_requested = False
            self._task = asyncio.create_task(self._loop(), name=f"strategy-runner-{self.instance_id}")

    async def stop(self) -> None:
        self._stop_requested = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=30)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def _loop(self) -> None:
        instance = await self._load_instance()
        if instance is None:
            logger.error("Instancia %s no existe, no se puede arrancar", self.instance_id)
            return

        strategy_cls = registry.get(instance.strategy_key)
        strategy = strategy_cls(strategy_cls.params_model(**instance.params))

        try:
            limits = await _load_limits()
            await bybit_client.set_leverage(instance.symbol, limits.max_leverage)
        except Exception:
            logger.exception("No se pudo fijar el apalancamiento de %s", instance.symbol)

        last_seen_candle: dt.datetime | None = None
        logger.info("Instancia %s (%s %s %s) arrancada", instance.id, instance.strategy_key, instance.symbol, instance.timeframe)
        await log_event(self.instance_id, "started", f"Instancia encendida ({instance.symbol} {instance.timeframe}m)")

        while not self._stop_requested:
            try:
                await self._reconcile_exchange_position(instance)

                candles = await bybit_client.get_candles_df(instance.symbol, instance.timeframe, limit=1000)
                if len(candles) >= 2:
                    closed_candles = candles.iloc[:-1]  # la ultima vela puede seguir formandose
                    newest_ts = closed_candles.index[-1]
                    action = decide_candle(last_seen_candle, newest_ts)
                    if action == "skip_initial":
                        # esa vela cerro antes de encender la instancia: se espera a la proxima en vez de operar una senal vieja
                        last_seen_candle = newest_ts
                        await log_event(
                            self.instance_id, "started",
                            f"Esperando la próxima vela cerrada (la última fue {fmt_time(newest_ts, '%H:%M')}, {tz_label()}): "
                            "no se opera con señales anteriores al encendido",
                        )
                    elif action == "evaluate":
                        last_seen_candle = newest_ts
                        await self._on_new_candle(instance, strategy, closed_candles)

                interval_seconds = _INTERVAL_SECONDS.get(instance.timeframe, 900)
            except Exception as exc:
                logger.exception("Error en el loop de la instancia %s", self.instance_id)
                await log_event(self.instance_id, "error", f"Error en el ciclo: {exc}")
                interval_seconds = _POLL_SECONDS

            await asyncio.sleep(min(_POLL_SECONDS, interval_seconds))

        logger.info("Instancia %s detenida", self.instance_id)
        await log_event(self.instance_id, "stopped", "Instancia apagada")

    async def _load_instance(self) -> StrategyInstance | None:
        async with async_session() as session:
            return await session.get(StrategyInstance, self.instance_id)

    async def _closed_pnl_since(self, symbol: str, since: dt.datetime):
        """PnL cerrado de este trade: solo los registros de Bybit posteriores a su apertura."""
        return summarize_closed_pnl(await bybit_client.get_closed_pnl_records(symbol), since)

    async def _symbol_open_elsewhere(self, symbol: str) -> bool:
        async with async_session() as session:
            result = await session.execute(
                select(func.count()).select_from(Trade).where(
                    Trade.symbol == symbol, Trade.closed_at.is_(None), Trade.strategy_instance_id != self.instance_id
                )
            )
            return result.scalar_one() > 0

    async def _get_open_trade(self) -> Trade | None:
        async with async_session() as session:
            result = await session.execute(
                select(Trade).where(
                    Trade.strategy_instance_id == self.instance_id, Trade.closed_at.is_(None)
                )
            )
            return result.scalars().first()

    async def _reconcile_exchange_position(self, instance: StrategyInstance) -> None:
        """Si Bybit cerro la posicion solo (stop-loss o take-profit), o si
        el bot se reinicio con una posicion ya abierta que no tenia
        registrada, actualiza la base para que refleje la realidad."""
        open_trade = await self._get_open_trade()
        exchange_position = await bybit_client.get_open_position(instance.symbol)

        if open_trade is not None and exchange_position is None:
            closed_pnl = await self._closed_pnl_since(instance.symbol, open_trade.opened_at)
            exit_price = closed_pnl.avg_exit_price if closed_pnl else open_trade.entry_price
            pnl = closed_pnl.closed_pnl if closed_pnl else 0.0
            exit_time = closed_pnl.updated_time if closed_pnl else dt.datetime.now(dt.timezone.utc)
            async with async_session() as session:
                trade = await session.get(Trade, open_trade.id)
                trade.exit_price = exit_price
                trade.pnl = pnl
                trade.closed_at = exit_time
                trade.exit_reason = "stop_loss_o_take_profit (exchange)"
                await session.commit()
            logger.info("Instancia %s: posicion cerrada por el exchange, pnl=%.4f", self.instance_id, pnl)
            await log_event(self.instance_id, "closed", f"Cerrada por stop-loss/take-profit del exchange, PnL {pnl:+.4f} USD")

        elif open_trade is None and exchange_position is not None and not should_adopt_position(
            False, True, await self._symbol_open_elsewhere(instance.symbol)
        ):
            return  # la posicion es de otra instancia: no se toca

        elif open_trade is None and exchange_position is not None:
            logger.warning(
                "Instancia %s: hay una posicion abierta en Bybit sin registro local, se adopta.",
                self.instance_id,
            )
            async with async_session() as session:
                session.add(
                    Trade(
                        strategy_instance_id=self.instance_id,
                        symbol=instance.symbol,
                        side=exchange_position["side"],
                        entry_price=exchange_position["entry_price"],
                        qty=exchange_position["qty"],
                        opened_at=dt.datetime.now(dt.timezone.utc),
                    )
                )
                await session.commit()

    async def _on_new_candle(self, instance: StrategyInstance, strategy, candles) -> None:
        open_trade = await self._get_open_trade()
        position = None
        if open_trade is not None:
            position = Position(
                side=open_trade.side, entry_price=open_trade.entry_price, qty=open_trade.qty,
                stop_loss=open_trade.stop_loss, take_profit=open_trade.take_profit,
            )

        if getattr(strategy, "required_data", ()):
            # funding / open interest como columnas extra: se piden solo al cerrar una vela nueva
            try:
                candles, notes = await enrich_candles(candles, instance.symbol, instance.timeframe, strategy.required_data)
            except Exception as exc:  # noqa: BLE001
                await log_event(self.instance_id, "error", f"No se pudieron obtener los datos de funding/open interest: {exc}")
                return
            for note in notes:
                await log_event(self.instance_id, "error", note)

        equity = await self._current_equity(instance)
        signal = strategy.on_candle(StrategyContext(candles=candles, position=position, equity=equity))
        if signal is None:
            return

        price = float(candles["close"].iloc[-1])

        if signal.action == "close":
            if open_trade is not None:
                await self._close_position(instance, open_trade, reason="senal")
            return

        desired_side = "long" if signal.action == "buy" else "short"
        if open_trade is not None:
            if open_trade.side == desired_side:
                return  # ya estamos en esa direccion, no duplicar entrada
            await self._close_position(instance, open_trade, reason="flip")

        limits = await _load_limits()
        daily_pnl_pct = await self._daily_pnl_pct(instance)
        open_positions_count = await _global_open_positions_count()

        check = RiskManager(limits).evaluate_entry(signal, equity, price, daily_pnl_pct, open_positions_count)
        if not check.approved:
            logger.info("Instancia %s: senal rechazada por riesgo (%s)", self.instance_id, check.reason)
            await log_event(self.instance_id, "rejected", f"Señal de {desired_side} rechazada por riesgo: {check.reason}")
            return

        qty = await bybit_client.round_qty(instance.symbol, check.qty)
        if qty <= 0:
            logger.info("Instancia %s: tamano calculado por debajo del minimo del simbolo", self.instance_id)
            info = await bybit_client.get_instrument_info(instance.symbol)
            await log_event(
                self.instance_id, "rejected",
                f"Señal de {desired_side} descartada: el tamaño calculado ({check.qty:.6f}) es menor al mínimo de "
                f"{instance.symbol} ({info['min_qty']}). Subí el capital o el % de riesgo de la instancia.",
            )
            return

        side = "Buy" if desired_side == "long" else "Sell"
        try:
            order = await bybit_client.place_market_order(
                instance.symbol, side, qty, stop_loss=signal.stop_loss, take_profit=signal.take_profit
            )
        except Exception as exc:
            logger.exception("Instancia %s: fallo al enviar la orden", self.instance_id)
            await log_event(self.instance_id, "error", f"Falló el envío de la orden de {desired_side}: {exc}")
            return

        await asyncio.sleep(1.0)  # le da tiempo a Bybit a reportar el fill en get_open_position
        exchange_position = await bybit_client.get_open_position(instance.symbol)
        entry_price = exchange_position["entry_price"] if exchange_position else price
        filled_qty = exchange_position["qty"] if exchange_position else qty

        async with async_session() as session:
            session.add(
                Order(
                    strategy_instance_id=self.instance_id,
                    exchange_order_id=order.get("orderId"),
                    symbol=instance.symbol, side=side, order_type="Market",
                    qty=filled_qty, price=entry_price, status="filled",
                )
            )
            session.add(
                Trade(
                    strategy_instance_id=self.instance_id,
                    symbol=instance.symbol, side=desired_side,
                    entry_price=entry_price, qty=filled_qty,
                    stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                    opened_at=dt.datetime.now(dt.timezone.utc),
                )
            )
            await session.commit()
        logger.info("Instancia %s: abierta %s %s qty=%s @ %s", self.instance_id, desired_side, instance.symbol, filled_qty, entry_price)
        await log_event(
            self.instance_id, "opened",
            f"Abierta {desired_side} {filled_qty} {instance.symbol} @ {entry_price:,.2f}"
            + (f" · stop {signal.stop_loss:,.2f}" if signal.stop_loss else "")
            + (f" · TP {signal.take_profit:,.2f}" if signal.take_profit else "")
            + (f" · {signal.reason}" if signal.reason else ""),
        )

    async def _close_position(self, instance: StrategyInstance, open_trade: Trade, reason: str) -> None:
        side = "Sell" if open_trade.side == "long" else "Buy"
        try:
            await bybit_client.place_market_order(instance.symbol, side, open_trade.qty, reduce_only=True)
        except Exception:
            logger.exception("Instancia %s: fallo al cerrar la posicion", self.instance_id)
            return

        closed_pnl = None
        for _ in range(4):  # Bybit tarda un instante en publicar el cierre
            await asyncio.sleep(1.0)
            closed_pnl = await self._closed_pnl_since(instance.symbol, open_trade.opened_at)
            if closed_pnl is not None:
                break
        async with async_session() as session:
            trade = await session.get(Trade, open_trade.id)
            trade.exit_price = closed_pnl.avg_exit_price if closed_pnl else trade.entry_price
            trade.pnl = closed_pnl.closed_pnl if closed_pnl else 0.0
            trade.closed_at = dt.datetime.now(dt.timezone.utc)
            trade.exit_reason = reason
            await session.commit()
        logger.info("Instancia %s: posicion cerrada (%s)", self.instance_id, reason)
        pnl_text = f", PnL {closed_pnl.closed_pnl:+.4f} USD" if closed_pnl else ", PnL no disponible aún"
        await log_event(self.instance_id, "closed", f"Cerrada por {reason}{pnl_text}")

    async def _current_equity(self, instance: StrategyInstance) -> float:
        async with async_session() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(Trade.pnl), 0.0)).where(
                    Trade.strategy_instance_id == self.instance_id, Trade.closed_at.is_not(None)
                )
            )
            realized_pnl = result.scalar_one()
        return instance.initial_capital + realized_pnl

    async def _daily_pnl_pct(self, instance: StrategyInstance) -> float:
        today_start = day_start_utc()  # el dia del limite de perdida diaria empieza a medianoche de la hora local
        async with async_session() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(Trade.pnl), 0.0)).where(
                    Trade.strategy_instance_id == self.instance_id,
                    Trade.closed_at.is_not(None),
                    Trade.closed_at >= today_start,
                )
            )
            pnl_today = result.scalar_one()
        return pnl_today / instance.initial_capital * 100 if instance.initial_capital else 0.0
