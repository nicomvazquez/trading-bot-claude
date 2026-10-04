"""Agrega instancias y operaciones SINTETICAS a la base real, para tener algo que mostrar en una
presentacion sin esperar semanas de operativa real. A diferencia de seed_demo.py (pensado para la
base de demostracion aparte), este script es ADITIVO: nunca borra ni toca una fila existente.

    docker compose exec app python -m app.tools.seed_presentation

Salvaguardas:
- Se niega a correr sobre una base cuyo nombre termine en "_demo" (para eso ya esta seed_demo.py,
  que ademas borra todo; no tiene sentido duplicar datos sinteticos ahi).
- Se niega si alguno de los nombres de instancia que va a crear ya existe (nunca pisa una fila real).
- Todas las instancias se crean con is_active=False: no hay riesgo de que el orquestador las
  encienda y manden ordenes reales a Bybit con esta configuracion de prueba.
- No toca BotSettings (la fila real de limites/kill-switch/entorno queda intacta).

Antes de correrlo se hizo un dump completo de la base real (ver backups/) por las dudas."""

import asyncio

from sqlalchemy import select

from app.config import settings
from app.db.base import async_session
from app.db.models import BacktestRun, Order, StrategyEvent, StrategyInstance, Trade
from app.tools.demo_data import SPECS, generate


class RefusedToSeed(RuntimeError):
    pass


async def main() -> None:
    if settings.db_name.endswith("_demo"):
        raise RefusedToSeed(
            f"«{settings.db_name}» ya es una base de demostración: usá seed_demo.py (borra y siembra), "
            "este script es para sumar datos de ejemplo a la base real."
        )

    wanted_names = {spec.name for spec in SPECS}
    async with async_session() as session:
        existing = set((await session.execute(
            select(StrategyInstance.name).where(StrategyInstance.name.in_(wanted_names))
        )).scalars())
    if existing:
        raise RefusedToSeed(
            f"Ya existen instancias con estos nombres en «{settings.db_name}»: {', '.join(sorted(existing))}. "
            "Se aborta para no pisar nada; borralas a mano primero si de verdad querés volver a sembrar."
        )

    data = generate()
    async with async_session() as session:
        ids: dict[str, int] = {}
        for row in data["instances"]:
            assert row["is_active"] is False, "las instancias de ejemplo nunca deben crearse activas"
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
    print(
        f"Agregado a «{settings.db_name}»: {len(data['instances'])} instancias de ejemplo "
        f"({', '.join(sorted(ids))}), {closed} operaciones cerradas, {len(data['trades']) - closed} abiertas, "
        f"{len(data['orders'])} órdenes, {len(data['events'])} eventos, {len(data['runs'])} backtests guardados. "
        "Todas is_active=False: no van a operar."
    )


if __name__ == "__main__":
    asyncio.run(main())
