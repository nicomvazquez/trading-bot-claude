"""Corre un backtest REAL (motor real, datos historicos reales de Bybit) para una estrategia y
guarda sus resultados como si fueran la operativa de una instancia: crea la instancia (apagada),
sus trades, ordenes y bitacora a partir de las operaciones que el backtest realmente genero.

A diferencia de seed_presentation.py (operaciones sorteadas al azar segun una tasa de aciertos),
aca el origen de cada trade es una simulacion real sobre precios reales: el "como le hubiera ido"
es genuino, lo unico que se inventa es la narrativa de que una instancia ya viene operando asi. La
corrida tambien queda guardada en el historial del backtester (History), porque es una corrida real.

Si el ultimo trade del backtest seguia abierto cuando termino la ventana, se guarda como posicion
ABIERTA de la instancia (no se fuerza su cierre): es mas realista para algo que "sigue operando".

Uso:
    docker compose exec app python -m app.tools.seed_real_backtest_instance <strategy_key> <symbol> <timeframe> <dias> [nombre]

Ejemplo:
    docker compose exec app python -m app.tools.seed_real_backtest_instance sma_cross BTCUSDT 15 90
"""

import asyncio
import datetime as dt
import sys
import uuid

from sqlalchemy import select

from app.backtest.config import BacktestConfig, ExecutionConfig
from app.backtest.history import run_fields
from app.backtest.service import run_backtest
from app.config import settings
from app.db.base import async_session
from app.db.models import BacktestRun, Order, StrategyEvent, StrategyInstance, Trade
from app.strategies import registry


class RefusedToSeed(RuntimeError):
    pass


async def main(strategy_key: str, symbol: str, timeframe: str, days: int, instance_name: str | None) -> None:
    if settings.db_name.endswith("_demo"):
        raise RefusedToSeed("Esta base ya es de demostración: no hace falta este script ahí (usá seed_demo.py).")

    strategy_cls = registry.get(strategy_key)
    params = strategy_cls.params_model()
    name = instance_name or f"{strategy_key}-{symbol.lower()}-real"

    async with async_session() as session:
        exists = (await session.execute(select(StrategyInstance.id).where(StrategyInstance.name == name))).scalar_one_or_none()
    if exists:
        raise RefusedToSeed(f"Ya existe una instancia llamada «{name}»: elegí otro nombre (quinto argumento) o borrala primero.")

    config = BacktestConfig(
        symbol=symbol, timeframe=timeframe, days=days, initial_capital=1000.0,
        execution=ExecutionConfig(funding_mode="historical"),
    )
    print(f"Corriendo backtest real: {strategy_key} {symbol} {timeframe} ({days} días)...", flush=True)
    output = await run_backtest(strategy_cls, params, config)
    result, metrics = output.result, output.metrics
    print(f"Backtest terminado: {metrics.get('num_trades')} operaciones, retorno {metrics.get('total_return_pct', 0):.2f}%.", flush=True)

    async with async_session() as session:
        instance = StrategyInstance(
            name=name, strategy_key=strategy_key, symbol=symbol, timeframe=timeframe,
            params=params.model_dump(), initial_capital=config.initial_capital, is_active=False,
            created_at=output.market.start - dt.timedelta(hours=2),
        )
        session.add(instance)
        await session.flush()

        events: list[tuple[dt.datetime, str, str]] = [
            (output.market.start, "started", f"Instancia encendida ({symbol} {timeframe}m)"),
        ]
        recent = set(id(t) for t in result.trades[-6:])

        for t in result.trades:
            is_open = t.open_at_end  # ver docstring: no se fuerza el cierre, queda como posicion real abierta
            session.add(Trade(
                strategy_instance_id=instance.id, symbol=symbol, side=t.side,
                entry_price=t.entry_price, exit_price=None if is_open else t.exit_price, qty=t.qty,
                pnl=None if is_open else t.pnl, stop_loss=t.stop_loss, take_profit=t.take_profit,
                exit_reason=None if is_open else t.exit_reason,
                opened_at=t.entry_time, closed_at=None if is_open else t.exit_time,
                fees=None if is_open else t.fees, funding=None if is_open else t.funding,
            ))

            entry_side = "Buy" if t.side == "long" else "Sell"
            session.add(Order(
                strategy_instance_id=instance.id, symbol=symbol, side=entry_side, order_type="Market",
                qty=t.qty, price=t.entry_price, status="filled", created_at=t.entry_time,
                exchange_order_id=str(uuid.uuid4()),
            ))
            if not is_open:
                session.add(Order(
                    strategy_instance_id=instance.id, symbol=symbol, side="Sell" if entry_side == "Buy" else "Buy",
                    order_type="Market", qty=t.qty, price=t.exit_price, status="filled", created_at=t.exit_time,
                    exchange_order_id=str(uuid.uuid4()),
                ))

            if id(t) in recent:
                sl = f" · stop {t.stop_loss:,.2f}" if t.stop_loss else ""
                events.append((t.entry_time, "opened", f"Abierta {t.side} {t.qty:g} {symbol} @ {t.entry_price:,.2f}{sl}"))
                if not is_open:
                    events.append((t.exit_time, "closed", f"Cerrada por {t.exit_reason}, PnL {t.pnl:+.4f} USD"))

        for moment, kind, message in events:
            session.add(StrategyEvent(strategy_instance_id=instance.id, timestamp=moment, kind=kind, message=message))

        session.add(BacktestRun(**run_fields(output, params.model_dump())))

        await session.commit()
        instance_id = instance.id

    closed = sum(1 for t in result.trades if not t.open_at_end)
    print(
        f"Instancia «{name}» (id {instance_id}) creada, apagada. {closed} operaciones cerradas, "
        f"{len(result.trades) - closed} abierta(s). La corrida real queda guardada en Backtesting → History."
    )


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 4:
        print("Uso: python -m app.tools.seed_real_backtest_instance <strategy_key> <symbol> <timeframe> <dias> [nombre]")
        raise SystemExit(1)
    asyncio.run(main(args[0], args[1], args[2], int(args[3]), args[4] if len(args) > 4 else None))
