from nicegui import ui

from app.config import settings
from app.db.base import async_session
from app.db.models import BotSettings
from app.exchange.bybit_client import bybit_client
from app.live import environment as env_svc
from app.live.alerts import alerts_configured, send_alert
from app.live.closing import close_many, load_open_trades
from app.live.queries import load_instances
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
    demo = bybit_client.is_demo
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
                if event.value:
                    await send_alert("⛔ Kill-switch activado: ninguna estrategia va a abrir operaciones nuevas hasta que lo desactives.")

            kill_switch.on_value_change(on_kill)

            ui.separator()
            with ui.row().classes("w-full items-center justify-between no-wrap gap-4"):
                with ui.column().classes("gap-1"):
                    ui.label("Cerrar todas las posiciones").classes("text-base font-semibold")
                    ui.label(
                        "Cierra YA, con una orden de mercado, cada posición abierta de cualquier instancia (esté corriendo o "
                        "no). No apaga las instancias ni activa el kill-switch por sí solo: si también querés que dejen de "
                        "operar, activalo arriba."
                    ).classes("text-sm text-gray-500 max-w-2xl")
                close_all_button = ui.button("Cerrar todo", icon="dangerous").props("unelevated no-caps color=negative")

            async def open_close_all_dialog() -> None:
                trades = await load_open_trades()
                if not trades:
                    ui.notify("No hay ninguna posición abierta.", type="info")
                    return
                names = {i.id: i.name for i in await load_instances()}
                with ui.dialog() as dialog, ui.card().classes("gap-3 p-5 w-[480px] max-w-full"):
                    ui.label(f"¿Cerrar {len(trades)} posición{'es' if len(trades) != 1 else ''}?").classes("text-lg font-bold")
                    ui.label("Se envía una orden de mercado por cada una. Esto no se puede deshacer.").classes("text-sm text-gray-600")
                    with ui.column().classes("w-full gap-1"):
                        for t in trades:
                            side_label = "▲ Long" if t.side == "long" else "▼ Short"
                            ui.label(f"{names.get(t.strategy_instance_id, '?')} · {t.symbol} · {side_label} · {t.qty:g}").classes("text-sm")
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Cancelar", on_click=lambda: dialog.submit(False)).props("flat no-caps")
                        ui.button("Cerrar todo", on_click=lambda: dialog.submit(True)).props("unelevated no-caps color=negative")
                if not await dialog:
                    dialog.delete()
                    return
                dialog.delete()
                close_all_button.props("loading")
                try:
                    results = await close_many(trades)
                finally:
                    close_all_button.props(remove="loading")
                ok = sum(1 for r in results if r["ok"])
                if ok == len(results):
                    ui.notify(f"Se cerraron las {ok} posiciones.", type="positive")
                else:
                    failed = ", ".join(r["symbol"] for r in results if not r["ok"])
                    ui.notify(f"Se cerraron {ok} de {len(results)}. Fallaron: {failed}. Mirá la actividad de esas instancias.",
                              type="negative", timeout=10000)

            close_all_button.on_click(open_close_all_dialog)

        with ui.element("div").classes("grid grid-cols-1 md:grid-cols-2 gap-6 w-full items-start"):
            # ------------------------------------------------------ conexion
            with w.bordered_card("gap-3"):
                w.section_title("Conexión con Bybit", "Cuenta y credenciales que usa el bot para operar.")
                active_key = settings.bybit_api_key if demo else settings.bybit_mainnet_api_key
                with ui.column().classes("w-full gap-2"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("Entorno").classes("text-sm text-gray-600")
                        pill("Demo Trading" if demo else "MAINNET · dinero real", "warn" if demo else "bad")
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("API key").classes("text-sm text-gray-600")
                        if active_key:
                            pill("Configurada", "good")
                        else:
                            pill("Falta configurar", "bad")
                if not active_key:
                    w.notice(
                        "Creá la clave en bybit.com (perfil → API → Create New Key"
                        + (" activando 'Demo Trading' antes de generarla" if demo else "")
                        + f"). Completá BYBIT_{'API' if demo else 'MAINNET_API'}_KEY y "
                        f"BYBIT_{'API' if demo else 'MAINNET_API'}_SECRET en el archivo .env y reiniciá la app.", "warning",
                    )

                env_result_box = ui.column().classes("w-full")
                env_target = "mainnet" if demo else "demo"
                env_target_label = "MAINNET (dinero real)" if demo else "Demo Trading"

                async def open_env_dialog() -> None:
                    ok, reason = await env_svc.can_switch_to(env_target)
                    env_result_box.clear()
                    if not ok:
                        with env_result_box:
                            w.notice(reason, "negative")
                        return
                    with ui.dialog() as dialog, ui.card().classes("gap-3 p-5 w-[480px] max-w-full"):
                        ui.label(f"¿Cambiar a {env_target_label}?").classes("text-lg font-bold")
                        if env_target == "mainnet":
                            w.notice(
                                "A partir de este cambio, toda instancia que enciendas va a operar con dinero real "
                                "en tu cuenta de Bybit. No hay ninguna posición abierta ni instancia corriendo ahora.",
                                "warning",
                            )
                        else:
                            ui.label("Volvés a operar con los fondos virtuales de Demo Trading.").classes("text-sm text-gray-600")
                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Cancelar", on_click=lambda: dialog.submit(False)).props("flat no-caps")
                            ui.button(f"Cambiar a {env_target_label}", on_click=lambda: dialog.submit(True)).props(
                                f"unelevated no-caps color={'negative' if env_target == 'mainnet' else 'primary'}"
                            )
                    if not await dialog:
                        dialog.delete()
                        return
                    dialog.delete()
                    env_button.props("loading")
                    try:
                        await env_svc.switch_to(env_target)
                    except env_svc.EnvironmentError as exc:
                        env_button.props(remove="loading")
                        with env_result_box:
                            w.notice(str(exc), "negative")
                        return
                    ui.notify(f"Entorno cambiado a {env_target_label}.", type="positive")
                    ui.navigate.to("/configuracion")

                env_button = ui.button(
                    f"Cambiar a {env_target_label}", icon="swap_horiz", on_click=open_env_dialog,
                ).props("outline no-caps color=" + ("negative" if demo else "primary"))

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

            # ------------------------------------------------------ alertas
            with w.bordered_card("gap-3"):
                w.section_title(
                    "Alertas (Telegram)",
                    "Avisa cuando algo falla de verdad: una orden que no se pudo enviar o cerrar, un error en el ciclo de "
                    "una instancia, o cuando activás el kill-switch. No detecta que el proceso del bot se caiga entero.",
                )
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Estado").classes("text-sm text-gray-600")
                    pill("Configuradas", "good") if alerts_configured() else pill("No configuradas", "idle")
                if not alerts_configured():
                    w.notice(
                        "Hablale a @BotFather en Telegram (/newbot) para conseguir un token, escribile un mensaje a tu bot "
                        "nuevo, y conseguí tu chat_id (por ejemplo con @userinfobot). Completá TELEGRAM_BOT_TOKEN y "
                        "TELEGRAM_CHAT_ID en el archivo .env y reiniciá la app.", "warning",
                    )
                alert_result = ui.column().classes("w-full")

                async def send_test_alert() -> None:
                    alert_result.clear()
                    test_alert_button.props("loading")
                    try:
                        ok = await send_alert("🔔 Alerta de prueba del bot de trading. Si ves esto, las alertas funcionan.")
                    finally:
                        test_alert_button.props(remove="loading")
                    with alert_result:
                        w.notice("Enviada. Revisá tu chat de Telegram." if ok else "No se pudo enviar. Revisá el token y el chat_id.",
                                 "positive" if ok else "negative")

                test_alert_button = ui.button("Enviar alerta de prueba", icon="send", on_click=send_test_alert).props("outline no-caps")
                if not alerts_configured():
                    test_alert_button.props("disable")

            # ------------------------------------------------------ limites
            with w.bordered_card("gap-2"):
                w.section_title("Límites de riesgo", "El Risk Manager los revisa antes de cada operación nueva; ninguna estrategia puede saltearlos.")
                max_daily_loss = _field(
                    "Pérdida diaria máxima por instancia (%)", "Si una instancia pierde más que esto en el día (de 00:00 a 24:00, hora argentina), deja de abrir operaciones hasta el día siguiente.",
                    value=bot_settings.max_daily_loss_pct, min=0.1, max=100.0, step=0.5,
                )
                max_daily_loss_global = _field(
                    "Pérdida diaria máxima GLOBAL (%)",
                    "Suma el PnL de hoy de TODAS las instancias contra el capital de las que están activas. Si se supera, "
                    "ninguna instancia abre operaciones nuevas por el resto del día. Dejalo vacío para no usar este límite.",
                    value=bot_settings.max_daily_loss_global_pct, min=0.1, max=100.0, step=0.5,
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
                        max_daily_loss_global_pct=None if max_daily_loss_global.value in (None, "") else float(max_daily_loss_global.value),
                        max_concurrent_positions=int(max_positions.value or 1),
                        max_leverage=float(max_leverage.value or 1),
                    )
                    ui.notify("Límites guardados. Se aplican a la próxima señal.", type="positive")

                ui.button("Guardar límites", icon="save", on_click=save).props("unelevated no-caps").classes("mt-1")
