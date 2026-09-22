from nicegui import ui

from app.ui.layout import render_nav


@ui.page("/operaciones")
def operaciones_page() -> None:
    render_nav("/operaciones")
    with ui.column().classes("w-full p-4 gap-4"):
        ui.label("Operaciones").classes("text-xl font-bold")
        with ui.card():
            ui.label(
                "Aca vas a ver la tabla de todas las ordenes y trades ejecutados, "
                "filtrable por estrategia, simbolo y fecha. Se habilita en la Fase 4, "
                "una vez que el bot ya este operando en Demo Trading (Fase 3)."
            ).classes("text-sm text-gray-500")
