from nicegui import ui

PAGES = [
    ("/", "Overview"),
    ("/operaciones", "Operaciones"),
    ("/estrategias", "Estrategias"),
    ("/backtesting", "Backtesting"),
    ("/configuracion", "Configuracion"),
]


def render_nav(active_path: str) -> None:
    with ui.header().classes("items-center justify-between"):
        ui.label("Bot Trading - Bybit Futures").classes("text-lg font-bold")
        with ui.row().classes("gap-4"):
            for path, label in PAGES:
                link = ui.link(label, path).classes("text-white")
                if path == active_path:
                    link.classes("font-bold underline")
