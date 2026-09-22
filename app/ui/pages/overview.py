from nicegui import ui

from app.ui.layout import render_nav


@ui.page("/")
def overview_page() -> None:
    render_nav("/")
    with ui.column().classes("w-full p-4 gap-4"):
        ui.label("Estado del bot").classes("text-xl font-bold")
        with ui.card():
            ui.label("Todavia no hay instancias de estrategia corriendo.")
            ui.label(
                "Esta pagina va a mostrar la curva de equity global, PnL del dia "
                "y las posiciones abiertas una vez que conectemos el motor de ejecucion (Fase 3)."
            ).classes("text-sm text-gray-500")
