from nicegui import ui

from app.config import settings
from app.db.base import async_session
from app.db.models import BotSettings
from app.exchange.bybit_client import bybit_client
from app.ui.layout import render_nav


async def _load_settings() -> BotSettings:
    async with async_session() as session:
        row = await session.get(BotSettings, 1)
        if row is None:
            row = BotSettings(id=1)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row


async def _save_settings(**fields) -> None:
    async with async_session() as session:
        row = await session.get(BotSettings, 1)
        if row is None:
            row = BotSettings(id=1)
            session.add(row)
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()


@ui.page("/configuracion")
async def configuracion_page() -> None:
    render_nav("/configuracion")

    with ui.column().classes("w-full p-4 gap-4"):
        ui.label("Configuracion").classes("text-xl font-bold")

        with ui.card():
            ui.label("Conexion a Bybit").classes("font-bold")
            env_label = "DEMO TRADING" if settings.bybit_demo else "MAINNET"
            ui.label(f"Entorno actual: {env_label}").classes(
                "text-orange-600" if settings.bybit_demo else "text-red-600 font-bold"
            )
            if not settings.bybit_api_key:
                ui.label(
                    "No hay API key configurada. Se crea desde tu cuenta normal de "
                    "bybit.com (perfil -> API -> Create New Key), activando el toggle "
                    "'Demo Trading' antes de generarla. Completa BYBIT_API_KEY y "
                    "BYBIT_API_SECRET en el archivo .env y reinicia la app."
                ).classes("text-sm text-gray-500")

            result_label = ui.label("")

            async def test_connection() -> None:
                result_label.text = "Probando..."
                try:
                    data = await bybit_client.check_connection()
                    accounts = data.get("list", [])
                    equity = accounts[0].get("totalEquity") if accounts else "N/A"
                    result_label.text = f"Conexion OK. Equity total (UNIFIED): {equity}"
                    result_label.classes(replace="text-green-600")
                except Exception as exc:  # noqa: BLE001
                    result_label.text = f"Error de conexion: {exc}"
                    result_label.classes(replace="text-red-600")

            with ui.row():
                ui.button("Probar conexion", on_click=test_connection)
                if settings.bybit_demo:
                    funds_result = ui.label("")

                    async def request_funds() -> None:
                        funds_result.text = "Pidiendo fondos..."
                        try:
                            await bybit_client.request_demo_funds()
                            funds_result.text = "Fondos virtuales acreditados. Actualiza el equity con 'Probar conexion'."
                            funds_result.classes(replace="text-green-600")
                        except Exception as exc:  # noqa: BLE001
                            funds_result.text = f"Error: {exc}"
                            funds_result.classes(replace="text-red-600")

                    ui.button("Pedir fondos demo", on_click=request_funds).props("outline")

        bot_settings = await _load_settings()

        with ui.card():
            ui.label("Limites de riesgo globales").classes("font-bold")
            ui.label(
                "Se aplican a TODAS las instancias activas. El Risk Manager los revisa "
                "antes de cada operacion nueva; nunca se puede saltear desde una estrategia."
            ).classes("text-xs text-gray-500")

            kill_switch = ui.switch("Kill-switch (corta toda apertura de operaciones nuevas)", value=bot_settings.kill_switch)
            kill_switch.classes("text-red-600" if bot_settings.kill_switch else "")

            max_daily_loss = ui.number("Perdida diaria maxima por instancia (%)", value=bot_settings.max_daily_loss_pct, min=0.1, max=100.0)
            max_positions = ui.number("Posiciones simultaneas maximas (global)", value=bot_settings.max_concurrent_positions, min=1, max=50)
            max_leverage = ui.number("Apalancamiento maximo", value=bot_settings.max_leverage, min=1, max=100)

            save_result = ui.label("")

            async def save() -> None:
                await _save_settings(
                    kill_switch=kill_switch.value,
                    max_daily_loss_pct=max_daily_loss.value,
                    max_concurrent_positions=int(max_positions.value),
                    max_leverage=max_leverage.value,
                )
                save_result.text = "Guardado."
                save_result.classes(replace="text-green-600")
                kill_switch.classes(replace="text-red-600" if kill_switch.value else "")

            ui.button("Guardar limites", on_click=save)
