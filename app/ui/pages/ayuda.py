"""Ayuda: los manuales (app/docs/*.md) dentro de la propia aplicacion."""

import pathlib

from nicegui import ui

from app.ui.layout import page_content, render_nav

DOCS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "docs"

# clave -> (titulo de la pestana, icono, archivo)
GUIDES = {
    "manual": ("Manual de uso", "menu_book", "manual_de_uso.md"),
    "estrategias": ("Estrategias", "psychology", "estrategias.md"),
    "backtester": ("Backtester", "science", "backtester.md"),
}


def _read(filename: str) -> str:
    try:
        return (DOCS_DIR / filename).read_text(encoding="utf-8")
    except OSError:
        return f"# No se pudo cargar la guía\n\nNo se encontró `{filename}` en `app/docs/`."


@ui.page("/ayuda")
def ayuda_page(guia: str = "manual") -> None:
    render_nav("/ayuda")
    current = guia if guia in GUIDES else "manual"
    with page_content("Ayuda", "Cómo usar el sistema, qué hace cada estrategia y cómo usar y leer el backtester.", width="max-w-5xl"):
        with ui.tabs(value=current).props("align=left no-caps inline-label").classes("w-full") as tabs:
            for key, (title, icon, _) in GUIDES.items():
                ui.tab(key, label=title, icon=icon)
        with ui.tab_panels(tabs, value=current, animated=False).classes("w-full bg-transparent"):
            for key, (_, _, filename) in GUIDES.items():
                with ui.tab_panel(key).classes("p-0 pt-4"):
                    with ui.card().props("flat bordered").classes("w-full p-6 md:p-8"):
                        ui.markdown(_read(filename), extras=["fenced-code-blocks", "tables", "cuddled-lists"]).classes("manual")
