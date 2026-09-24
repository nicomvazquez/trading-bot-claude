"""Boton «Descargar» con menu Excel / CSV, reutilizable en cualquier pantalla."""

import datetime as dt
import inspect
import logging
import re
from typing import Awaitable, Callable

from nicegui import ui

from app.timeutil import fmt
from app.exports import Table, to_csv, to_xlsx

logger = logging.getLogger(__name__)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _filename(base: str, extension: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_") or "datos"
    return f"{safe}_{fmt(dt.datetime.now(dt.timezone.utc), '%Y-%m-%d_%H%M')}.{extension}"


def export_button(
    get_tables: Callable[[], list[Table] | Awaitable[list[Table]]],
    filename: str,
    csv_tables: list[str],
    label: str = "Descargar",
) -> ui.button:
    """`get_tables` se llama al hacer clic (con los datos y filtros de ese momento; puede ser async).
    Excel lleva todas las hojas; el CSV es de una sola tabla, por eso hay una opcion por cada nombre de `csv_tables`."""

    async def collect() -> list[Table] | None:
        try:
            tables = get_tables()
            if inspect.isawaitable(tables):
                tables = await tables
        except Exception as exc:  # noqa: BLE001
            logger.exception("No se pudieron preparar los datos para descargar")
            ui.notify(f"No se pudo preparar la descarga: {exc}", type="negative")
            return None
        if not any(t.rows for t in tables):
            ui.notify("No hay datos para descargar.", type="warning")
            return None
        return tables

    async def download_xlsx() -> None:
        if (tables := await collect()) is not None:
            ui.download.content(to_xlsx(tables), _filename(filename, "xlsx"), XLSX_TYPE)

    async def download_csv(name: str) -> None:
        if (tables := await collect()) is None:
            return
        table = next((t for t in tables if t.name == name), None)
        if table is None or not table.rows:
            ui.notify(f"No hay datos en «{name}».", type="warning")
            return
        ui.download.content(to_csv(table), _filename(f"{filename}_{name}", "csv"), "text/csv;charset=utf-8")

    with ui.button(label, icon="download").props("outline dense no-caps") as button:
        with ui.menu():
            ui.menu_item("Excel (.xlsx)" + (" · todas las hojas" if len(csv_tables) > 1 else ""), on_click=download_xlsx)
            ui.separator()
            for name in csv_tables:
                ui.menu_item(f"CSV · {name}" if len(csv_tables) > 1 else "CSV (.csv)", on_click=lambda n=name: download_csv(n))
    return button
