"""Siembra la base de DEMOSTRACION con operaciones de ejemplo.

    docker compose --profile demo run --rm app-demo python -m app.tools.seed_demo

Borra y recrea todas las tablas de la base configurada, por eso se niega a correr salvo que su nombre termine
en «_demo» (ver demo_data.assert_demo_database). La base real nunca se toca."""

import asyncio

from app.config import settings
from app.db.base import Base, async_session, engine, init_db
from app.db.models import BacktestRun, BotSettings, Order, StrategyEvent, StrategyInstance, Trade
from app.tools.demo_data import assert_demo_database, generate


async def main() -> None:
    assert_demo_database(settings.db_name)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db()

    data = generate()
    async with async_session() as session:
        session.add(BotSettings(id=1))
        ids: dict[str, int] = {}
        for row in data["instances"]:
            instance = StrategyInstance(**row)
            session.add(instance)
            await session.flush()
            ids[row["name"]] = instance.id
        for row in data["trades"]:
            session.add(Trade(strategy_instance_id=ids[row["instance"]], **{k: v for k, v in row.items() if k != "instance"}))
        for row in data["orders"]:
            session.add(Order(strategy_instance_id=ids[row["instance"]], **{k: v for k, v in row.items() if k != "instance"}))
        for row in data["events"]:
            session.add(StrategyEvent(strategy_instance_id=ids[row["instance"]], **{k: v for k, v in row.items() if k != "instance"}))
        for row in data["runs"]:
            session.add(BacktestRun(**row))
        await session.commit()

    closed = sum(1 for t in data["trades"] if t["closed_at"])
    print(f"Base «{settings.db_name}» sembrada: {len(data['instances'])} instancias, {closed} operaciones cerradas, "
          f"{len(data['trades']) - closed} abiertas, {len(data['orders'])} órdenes, {len(data['events'])} eventos, {len(data['runs'])} backtests.")


if __name__ == "__main__":
    asyncio.run(main())
