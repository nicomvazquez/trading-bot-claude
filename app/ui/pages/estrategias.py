import datetime as dt
import logging

from nicegui import ui

from app.config import settings
from app.live import instances as svc
from app.live.events import load_events
from app.live.orchestrator import orchestrator
from app.live.queries import load_instances, load_trades
from app.live.stats import summarize
from app.strategies import registry
from app.ui import backtest_widgets as w
from app.ui.backtest_format import fmt_pct, fmt_usd, sign_class
from app.ui.layout import page_content, pill, render_nav
from app.ui.param_form import render_param_form

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 15
TIMEFRAMES = {"1": "1 minuto", "5": "5 minutos", "15": "15 minutos", "60": "1 hora", "240": "4 horas", "D": "1 día"}
EVENT_STYLE = {
    "started": ("play_circle", "text-green-700"),
    "stopped": ("stop_circle", "text-gray-500"),
    "opened": ("trending_up", "text-blue-700"),
    "closed": ("check_circle", "text-gray-700"),
    "rejected": ("block", "text-amber-700"),
    "error": ("error", "text-red-700"),
}


def _ago(moment: dt.datetime | None) -> str:
    if moment is None:
        return "—"
    seconds = (dt.datetime.now(dt.timezone.utc) - moment).total_seconds()
    if seconds < 90:
        return "hace instantes"
    if seconds < 3600:
        return f"hace {int(seconds // 60)} min"
    if seconds < 86400:
        return f"hace {int(seconds // 3600)} h"
    return moment.strftime("%d/%m %H:%M")


def _param_chips(strategy_cls, params: dict) -> None:
    fields = strategy_cls.params_model.model_fields
    with ui.row().classes("gap-1 flex-wrap"):
        for key, value in params.items():
            label = (fields[key].description or key) if key in fields else key
            text = ("sí" if value else "no") if isinstance(value, bool) else f"{value:g}" if isinstance(value, float) else str(value)
            ui.chip(f"{key}: {text}").props("outline dense size=sm color=grey-7").tooltip(label)


def _stat(label: str, value: str, color: str = "") -> None:
    with ui.column().classes("gap-0 min-w-[96px]"):
        ui.label(label).classes("text-xs uppercase tracking-wide text-gray-500")
        ui.label(value).classes(f"text-base font-semibold {color}")


def _sizing_notice(preview: svc.SizingPreview | None, error: str | None = None) -> None:
    if error:
        w.notice(error, "negative")
    elif preview is None:
        w.notice("El tamaño de cada operación depende del stop de cada señal, así que no se puede estimar de antemano.", "info")
    else:
        w.notice(preview.message, "positive" if preview.ok else "warning")


# ------------------------------------------------------------------ dialogo alta / edicion

def open_editor(root, strategy_key: str, refresh, instance=None) -> None:
    """Dialogo de alta (instance=None) o de edicion."""
    strategy_cls = registry.get(strategy_key)
    editing = instance is not None
    with root:
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().classes("w-[560px] max-w-full gap-3 p-5"):
            ui.label(f"{'Editar' if editing else 'Nueva instancia'}: {strategy_cls.display_name}").classes("text-lg font-bold")
            if strategy_cls.description:
                ui.label(strategy_cls.description).classes("text-sm text-gray-500")

            ui.label("General").classes("text-sm font-semibold text-gray-900 mt-1")
            name = ui.input("Nombre", value=instance.name if editing else "").props("outlined dense").classes("w-full")
            with ui.row().classes("w-full gap-3 no-wrap"):
                symbol = ui.input("Símbolo", value=instance.symbol if editing else "BTCUSDT").props("outlined dense").classes("flex-1")
                timeframe = ui.select(TIMEFRAMES, label="Timeframe", value=instance.timeframe if editing else "15").props(
                    "outlined dense").classes("flex-1")
            capital = ui.number(
                "Capital asignado (USD)", value=instance.initial_capital if editing else 1000.0, min=1, format="%.2f"
            ).props("outlined dense").classes("w-full")
            ui.label(
                "Capital virtual: no reserva fondos en Bybit, solo se usa para calcular el tamaño de cada operación."
            ).classes("text-xs text-gray-500 -mt-2")

            ui.label("Parámetros").classes("text-sm font-semibold text-gray-900 mt-1")
            with ui.column().classes("w-full gap-2"):
                get_params = render_param_form(strategy_cls.params_model, instance.params if editing else None)

            ui.label("Chequeo de tamaño").classes("text-sm font-semibold text-gray-900 mt-1")
            preview_box = ui.column().classes("w-full gap-2")
            error_box = ui.column().classes("w-full")
            if editing and orchestrator.is_running(instance.id):
                w.notice("La instancia está corriendo: al guardar se reinicia con los nuevos valores (la posición abierta se conserva).", "info")

            async def check() -> bool:
                preview_box.clear()
                try:
                    preview = await svc.sizing_preview(symbol.value.strip().upper(), float(capital.value or 0), get_params().model_dump())
                    error = None
                except svc.InstanceError as exc:
                    preview, error = None, str(exc)
                except Exception as exc:  # noqa: BLE001 - parametros invalidos del formulario
                    preview, error = None, f"Parámetros inválidos: {exc}"
                with preview_box:
                    _sizing_notice(preview, error)
                return error is None

            async def save() -> None:
                error_box.clear()
                try:
                    params = get_params().model_dump()
                    if editing:
                        await svc.update_instance(instance.id, name.value, symbol.value, timeframe.value, float(capital.value or 0), params)
                    else:
                        await svc.create_instance(strategy_key, name.value, symbol.value, timeframe.value, float(capital.value or 0), params)
                except svc.InstanceError as exc:
                    with error_box:
                        w.notice(str(exc), "negative")
                    return
                except Exception as exc:  # noqa: BLE001
                    with error_box:
                        w.notice(f"Parámetros inválidos: {exc}", "negative")
                    return
                dialog.close()
                ui.notify("Instancia guardada", type="positive")
                await refresh()

            with ui.row().classes("w-full justify-between mt-2"):
                ui.button("Verificar tamaño", icon="calculate", on_click=check).props("flat no-caps")
                with ui.row().classes("gap-2"):
                    ui.button("Cancelar", on_click=dialog.close).props("flat no-caps")
                    ui.button("Guardar cambios" if editing else "Crear instancia", icon="save", on_click=save).props("unelevated no-caps")
    dialog.on("hide", lambda: dialog.delete())
    dialog.open()
    if editing:
        ui.timer(0.2, check, once=True)


# ------------------------------------------------------------------ pagina

@ui.page("/estrategias")
async def estrategias_page() -> None:
    render_nav("/estrategias")
    root = ui.column().classes("w-full")
    open_panels: set[int] = set()  # instancias con la bitacora desplegada: no se refresca mientras se lee

    async def confirm(title: str, text: str, button: str = "Confirmar") -> bool:
        with root:
            with ui.dialog() as dialog, ui.card().classes("gap-3 p-5 w-[440px] max-w-full"):
                ui.label(title).classes("text-lg font-bold")
                ui.label(text).classes("text-sm text-gray-600")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Cancelar", on_click=lambda: dialog.submit(False)).props("flat no-caps")
                    ui.button(button, on_click=lambda: dialog.submit(True)).props("unelevated no-caps color=negative")
        result = await dialog
        dialog.delete()
        return bool(result)

    async def toggle(instance, active: bool) -> None:
        if active and not settings.bybit_demo:
            if not await confirm("Operar en MAINNET", f"«{instance.name}» va a operar con dinero real. ¿Confirmás?", "Encender"):
                await refresh()
                return
        try:
            await svc.set_active(instance.id, active)
        except svc.InstanceError as exc:
            ui.notify(str(exc), type="negative")
        await refresh()

    async def duplicate(instance) -> None:
        try:
            await svc.duplicate_instance(instance.id)
            ui.notify("Instancia duplicada (apagada)", type="positive")
        except svc.InstanceError as exc:
            ui.notify(str(exc), type="negative")
        await refresh()

    async def delete(instance) -> None:
        if not await confirm("Eliminar instancia", f"Se elimina «{instance.name}» y su bitácora. Esto no se puede deshacer.", "Eliminar"):
            return
        try:
            await svc.delete_instance(instance.id)
            ui.notify("Instancia eliminada", type="positive")
        except svc.InstanceError as exc:
            ui.notify(str(exc), type="negative")
        await refresh()

    async def render_instance(instance, trades: list) -> None:
        strategy_cls = registry.get(instance.strategy_key)
        running = orchestrator.is_running(instance.id)
        stats = summarize(trades)
        open_trade = next((t for t in trades if t.closed_at is None), None)
        events = await load_events(instance.id, limit=12)
        last = events[0] if events else None
        deletable, delete_reason = await svc.can_delete(instance.id)
        pnl_pct = stats["total_pnl"] / instance.initial_capital * 100 if instance.initial_capital else None

        with w.bordered_card("gap-3"):
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                with ui.column().classes("gap-0"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(instance.name).classes("text-base font-semibold text-gray-900")
                        if running:
                            pill("Corriendo", "good")
                        elif instance.is_active:
                            pill("Activa sin runner", "warn")
                        else:
                            pill("Apagada", "idle")
                    ui.label(f"{strategy_cls.display_name} · {instance.symbol} · {TIMEFRAMES.get(instance.timeframe, instance.timeframe)}").classes(
                        "text-sm text-gray-500")
                ui.switch("Activa", value=instance.is_active, on_change=lambda e, i=instance: toggle(i, e.value))

            with ui.row().classes("w-full gap-6 flex-wrap"):
                _stat("Capital", fmt_usd(instance.initial_capital))
                _stat("PnL realizado", f"{fmt_usd(stats['total_pnl'], signed=True)}" + (f" ({fmt_pct(pnl_pct, signed=True)})" if pnl_pct is not None else ""),
                      sign_class(stats["total_pnl"]))
                _stat("Hoy", fmt_usd(stats["pnl_today"], signed=True), sign_class(stats["pnl_today"]))
                _stat("Trades", str(stats["closed"]))
                _stat("Win rate", fmt_pct(stats["win_rate_pct"]) if stats["win_rate_pct"] is not None else "—")
                _stat("Profit factor", f"{stats['profit_factor']:.2f}" if stats["profit_factor"] else "—")
                if open_trade:
                    _stat("Posición", f"{'▲ Long' if open_trade.side == 'long' else '▼ Short'} {open_trade.qty:g} @ {open_trade.entry_price:,.2f}")
                else:
                    _stat("Posición", "Sin posición")
                _stat("Última actividad", _ago(last.timestamp) if last else "—")

            if last is not None and last.kind in ("rejected", "error"):
                w.notice(f"{_ago(last.timestamp).capitalize()}: {last.message}", "warning" if last.kind == "rejected" else "negative")

            _param_chips(strategy_cls, instance.params)

            with ui.row().classes("w-full items-center gap-1"):
                ui.button("Editar", icon="edit", on_click=lambda i=instance: open_editor(root, i.strategy_key, refresh, i)).props("flat dense no-caps")
                ui.button("Duplicar", icon="content_copy", on_click=lambda i=instance: duplicate(i)).props("flat dense no-caps")
                delete_btn = ui.button("Eliminar", icon="delete", on_click=lambda i=instance: delete(i)).props("flat dense no-caps color=negative")
                if not deletable:
                    delete_btn.props("disable")
                    with delete_btn:
                        ui.tooltip(delete_reason)

            with ui.expansion("Actividad reciente", icon="history", value=instance.id in open_panels).props("dense").classes("w-full").on_value_change(
                lambda e, i=instance.id: open_panels.add(i) if e.value else open_panels.discard(i)
            ):
                if not events:
                    ui.label("Todavía no hay actividad registrada. Aparece acá al encender la instancia.").classes("text-sm text-gray-500")
                for ev in events:
                    icon, color = EVENT_STYLE.get(ev.kind, ("info", "text-gray-600"))
                    with ui.row().classes("items-start no-wrap gap-2 w-full"):
                        ui.icon(icon, size="18px").classes(color)
                        ui.label(ev.timestamp.astimezone().strftime("%d/%m %H:%M")).classes("text-xs text-gray-500 w-24 shrink-0 pt-0.5")
                        ui.label(ev.message).classes("text-sm text-gray-800")

    async def refresh() -> None:
        instances = await load_instances()
        trades = await load_trades()
        by_instance: dict[int, list] = {}
        for trade in trades:
            by_instance.setdefault(trade.strategy_instance_id, []).append(trade)
        running = sum(1 for i in instances if orchestrator.is_running(i.id))

        body.clear()
        with body:
            with ui.row().classes("items-center gap-3"):
                ui.chip(f"{running} corriendo", icon="bolt").props("outline dense color=green" if running else "outline dense color=grey")
                ui.chip(f"{len(instances)} instancias", icon="layers").props("outline dense color=grey-8")
            if not instances:
                w.empty_state("smart_toy", "Todavía no creaste ninguna instancia. Elegí una estrategia arriba y creá la primera.")
            for instance in instances:
                await render_instance(instance, by_instance.get(instance.id, []))

    with root, page_content("Estrategias", "Creá instancias de cada estrategia con su propio símbolo, capital y parámetros, y controlalas desde acá."):
        with ui.column().classes("w-full gap-2"):
            w.section_title("Catálogo", "Elegí una estrategia para crear una instancia con su propio símbolo, capital y parámetros.")
            with ui.element("div").classes("grid grid-cols-1 md:grid-cols-3 gap-3 w-full"):
                for key, strategy_cls in registry.get_all().items():
                    with ui.card().props("flat bordered").classes("p-4 gap-2 justify-between"):
                        with ui.column().classes("gap-1"):
                            ui.label(strategy_cls.display_name).classes("font-semibold text-gray-900")
                            ui.label(strategy_cls.description or "").classes("text-sm text-gray-500")
                        ui.button("Crear instancia", icon="add", on_click=lambda k=key: open_editor(root, k, refresh)).props("outline dense no-caps")
        with ui.column().classes("w-full gap-2"):
            w.section_title("Mis instancias")
        body = ui.column().classes("w-full gap-3")

    await refresh()

    async def auto_refresh() -> None:
        if not open_panels:  # no pisar la bitacora que se esta leyendo
            await refresh()

    ui.timer(REFRESH_SECONDS, auto_refresh)
