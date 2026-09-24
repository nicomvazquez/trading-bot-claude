from nicegui import ui

from app.config import settings
from app.db.base import async_session
from app.db.models import BotSettings
from app.exchange.bybit_client import bybit_client
from app.ui import backtest_widgets as w
from app.ui.layout import page_content, pill, render_nav


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


def _field(label: str, hint: str, **kwargs) -> ui.number:
    field = ui.number(label, **kwargs).props("outlined dense").classes("w-full")
    ui.label(hint).classes("text-xs text-gray-500 -mt-2 mb-1")
    return field


@ui.page("/configuracion")
async def configuracion_page() -> None:
    render_nav("/configuracion")
    demo = settings.bybit_demo
    bot_settings = await _load_settings()

    with page_content("Configuración", "Conexión con Bybit y límites de riesgo que se aplican a todas las estrategias.", width="max-w-5xl"):
        # ---------------------------------------------------------- emergencia
        with w.bordered_card("gap-3"):
            with ui.row().classes("w-full items-center justify-between no-wrap gap-4"):
                with ui.column().classes("gap-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("gpp_maybe", size="22px").classes("text-[#b02a2a]")
                        ui.label("Kill-switch").classes("text-base font-semibold")
                        kill_pill = pill("ACTIVADO" if bot_settings.kill_switch else "Desactivado", "bad" if bot_settings.kill_switch else "idle")
                    ui.label(
                        "Corta la apertura de operaciones nuevas en todas las estrategias. Las posiciones que ya están abiertas "
                        "conservan su stop-loss y take-profit en el exchange. Se aplica al instante."
                    ).classes("text-sm text-gray-500 max-w-2xl")
                kill_switch = ui.switch(value=bot_settings.kill_switch).props("color=negative size=lg")

            async def on_kill(event) -> None:
                await _save_settings(kill_switch=bool(event.value))
                kill_pill.set_content(
                    f'<span class="pill pill-{"bad" if event.value else "idle"}"><span class="dot"></span>'
                    f'{"ACTIVADO" if event.value else "Desactivado"}</span>'
                )
                ui.notify("Kill-switch ACTIVADO: no se abrirán operaciones nuevas" if event.value else "Kill-switch desactivado",
                          type="negative" if event.value else "positive")

            kill_switch.on_value_change(on_kill)

        with ui.element("div").classes("grid grid-cols-1 md:grid-cols-2 gap-6 w-full items-start"):
            # ------------------------------------------------------ conexion
            with w.bordered_card("gap-3"):
                w.section_title("Conexión con Bybit", "Cuenta y credenciales que usa el bot para operar.")
                with ui.column().classes("w-full gap-2"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("Entorno").classes("text-sm text-gray-600")
                        pill("Demo Trading" if demo else "MAINNET · dinero real", "warn" if demo else "bad")
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("API key").classes("text-sm text-gray-600")
                        if settings.bybit_api_key:
                            pill("Configurada", "good")
                        else:
                            pill("Falta configurar", "bad")
                if not settings.bybit_api_key:
                    w.notice(
                        "Creá la clave en bybit.com (perfil → API → Create New Key) activando 'Demo Trading' antes de generarla. "
                        "Completá BYBIT_API_KEY y BYBIT_API_SECRET en el archivo .env y reiniciá la app.", "warning",
                    )
                result_box = ui.column().classes("w-full")

                async def test_connection() -> None:
                    result_box.clear()
                    test_button.props("loading")
                    try:
                        data = await bybit_client.check_connection()
                        accounts = data.get("list", [])
                        margin = float(accounts[0].get("totalMarginBalance") or 0) if accounts else 0.0
                        with result_box:
                            w.notice(f"Conexión correcta. Balance de margen: ${margin:,.2f}", "positive")
                    except Exception as exc:  # noqa: BLE001
                        with result_box:
                            w.notice(f"No se pudo conectar: {exc}", "negative")
                    finally:
                        test_button.props(remove="loading")

                async def request_funds() -> None:
                    result_box.clear()
                    try:
                        await bybit_client.request_demo_funds()
                        with result_box:
                            w.notice("Fondos virtuales acreditados. Probá la conexión para ver el balance actualizado.", "positive")
                    except Exception as exc:  # noqa: BLE001
                        with result_box:
                            w.notice(f"No se pudieron pedir fondos: {exc}", "negative")

                with ui.row().classes("gap-2"):
                    test_button = ui.button("Probar conexión", icon="wifi_tethering", on_click=test_connection).props("unelevated no-caps")
                    if demo:
                        ui.button("Pedir fondos demo", icon="add_card", on_click=request_funds).props("outline no-caps")

            # ------------------------------------------------------ limites
            with w.bordered_card("gap-2"):
                w.section_title("Límites de riesgo", "El Risk Manager los revisa antes de cada operación nueva; ninguna estrategia puede saltearlos.")
                max_daily_loss = _field(
                    "Pérdida diaria máxima por instancia (%)", "Si una instancia pierde más que esto en el día UTC, deja de abrir operaciones hasta el día siguiente.",
                    value=bot_settings.max_daily_loss_pct, min=0.1, max=100.0, step=0.5,
                )
                max_positions = _field(
                    "Posiciones simultáneas máximas", "Tope global, sumando todas las instancias.",
                    value=bot_settings.max_concurrent_positions, min=1, max=50, step=1, format="%.0f",
                )
                max_leverage = _field(
                    "Apalancamiento máximo (x)", "Tope de exposición: el nocional de una posición no supera capital × este valor. No define el tamaño.",
                    value=bot_settings.max_leverage, min=1, max=100, step=1,
                )

                async def save() -> None:
                    await _save_settings(
                        max_daily_loss_pct=float(max_daily_loss.value or 0.1),
                        max_concurrent_positions=int(max_positions.value or 1),
                        max_leverage=float(max_leverage.value or 1),
                    )
                    ui.notify("Límites guardados. Se aplican a la próxima señal.", type="positive")

                ui.button("Guardar límites", icon="save", on_click=save).props("unelevated no-caps").classes("mt-1")
