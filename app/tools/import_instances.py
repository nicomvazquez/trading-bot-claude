"""Importa instancias desde un JSON exportado de otra base. Las crea siempre APAGADAS: no traen
operaciones ni historial, y encenderlas es una decision aparte (desde el dashboard).

    docker compose run --rm -v /ruta/instancias.json:/tmp/instancias.json app python -m app.tools.import_instances /tmp/instancias.json

Si una instancia ya existe por nombre, se omite (no se pisa nada)."""

import asyncio
import json
import sys

from app.db.base import init_db
from app.live import instances as svc


async def main(path: str) -> None:
    await init_db()
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    created, skipped = 0, []
    for row in rows:
        try:
            await svc.create_instance(row["strategy_key"], row["name"], row["symbol"], row["timeframe"], row["capital"], row["params"])
            created += 1
        except svc.InstanceError as exc:
            skipped.append(f"{row['name']}: {exc}")
    print(f"creadas (apagadas): {created}")
    for line in skipped:
        print("omitida:", line)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python -m app.tools.import_instances <archivo.json>")
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1]))
