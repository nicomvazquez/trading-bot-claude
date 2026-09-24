from nicegui import ui

from app.live.queries import load_instances, load_orders, load_trades
from app.live.stats import summarize
from app.ui import backtest_widgets as w
from app.ui.backtest_format import EXIT_REASON_LABELS, fmt_pct, fmt_usd, sign_class
from app.ui.layout import page_content, render_nav

ALL = "todas"


def _fmt_dt(value) -> str:
    return value.astimezone().strftime("%d/%m %H:%M") if value else "—"


def _fmt_price(value) -> str:
    return f"{value:,.2f}" if value is not None else "—"


@ui.page("/operaciones")
def operaciones_page() -> None:
    render_nav("/operaciones")
    state = {"trades": [], "orders": [], "names": {}}

    with page_content("Operaciones", "Historial de trades y órdenes enviadas al exchange, con filtros por instancia, estado y lado."):
        with w.bordered_card():
            with ui.row().classes("w-full items-end gap-4"):
                instance = ui.select({ALL: "Todas las instancias"}, value=ALL, label="Instancia").props("outlined dense").classes("w-56")
                status = ui.select({ALL: "Todas", "open": "Abiertas", "closed": "Cerradas"}, value=ALL, label="Estado").props(
                    "outlined dense").classes("w-40")
                side = ui.select({ALL: "Long y short", "long": "Long", "short": "Short"}, value=ALL, label="Lado").props(
                    "outlined dense").classes("w-40")
                ui.button("Actualizar", icon="refresh", on_click=lambda: load()).props("flat no-caps")
            summary = ui.row().classes("w-full gap-6 text-sm")
        with w.bordered_card():
            w.section_title("Trades", "Cada fila es una posición completa (entrada y salida).")
            trades_box = ui.column().classes("w-full")
        with w.bordered_card():
            w.section_title("Órdenes enviadas", "Últimas 200 órdenes enviadas al exchange.")
            orders_box = ui.column().classes("w-full")

    def filtered() -> list:
        rows = state["trades"]
        if instance.value != ALL:
            rows = [t for t in rows if t.strategy_instance_id == instance.value]
        if status.value == "open":
            rows = [t for t in rows if t.closed_at is None]
        elif status.value == "closed":
            rows = [t for t in rows if t.closed_at is not None]
        if side.value != ALL:
            rows = [t for t in rows if t.side == side.value]
        return rows

    def render() -> None:
        rows = filtered()
        stats = summarize(rows)
        summary.clear()
        with summary:
            ui.label(f"{len(rows)} trades ({stats['open']} abiertos)")
            ui.label(f"PnL: {fmt_usd(stats['total_pnl'], signed=True)}").classes(sign_class(stats["total_pnl"]))
            ui.label(f"Win rate: {fmt_pct(stats['win_rate_pct']) if stats['win_rate_pct'] is not None else '—'}")
            ui.label(f"Profit factor: {stats['profit_factor']:.2f}" if stats["profit_factor"] else "Profit factor: —")

        trades_box.clear()
        with trades_box:
            if not rows:
                w.empty_state("receipt_long", "No hay trades con esos filtros.")
            else:
                table = ui.table(
                    columns=[{"name": k, "label": label, "field": k, "align": "left" if k in ("inst", "side", "reason", "opened", "closed_at") else "right",
                              "sortable": k in ("opened", "pnl")}
                             for k, label in [("opened", "Apertura"), ("inst", "Instancia"), ("symbol", "Símbolo"), ("side", "Lado"),
                                              ("qty", "Cantidad"), ("entry", "Entrada"), ("exit", "Salida"), ("sl", "Stop"), ("tp", "TP"),
                                              ("pnl", "PnL (USD)"), ("reason", "Motivo"), ("closed_at", "Cierre")]],
                    rows=[{
                        "id": t.id, "opened": _fmt_dt(t.opened_at), "inst": state["names"].get(t.strategy_instance_id, "?"),
                        "symbol": t.symbol, "side": t.side, "qty": t.qty, "entry": _fmt_price(t.entry_price),
                        "exit": _fmt_price(t.exit_price), "sl": _fmt_price(t.stop_loss), "tp": _fmt_price(t.take_profit),
                        "pnl": None if t.pnl is None else round(t.pnl, 4), "reason": EXIT_REASON_LABELS.get(t.exit_reason, t.exit_reason) if t.exit_reason else ("Abierta" if t.closed_at is None else "—"),
                        "closed_at": _fmt_dt(t.closed_at),
                    } for t in rows],
                    row_key="id", pagination=25,
                ).props("flat dense").classes("w-full")
                table.add_slot("body-cell-side", r"""<q-td :props="props"><span :class="props.value === 'long' ? 'side-long' : 'side-short'">{{ props.value === 'long' ? '▲ Long' : '▼ Short' }}</span></q-td>""")
                table.add_slot("body-cell-pnl", r"""
                    <q-td :props="props" :class="props.value > 0 ? 'text-[#006300]' : (props.value < 0 ? 'text-[#d03b3b]' : '')">
                        {{ props.value === null ? '—' : (props.value > 0 ? '+' : '') + props.value.toFixed(4) }}
                    </q-td>""")

        orders_box.clear()
        with orders_box:
            if not state["orders"]:
                ui.label("Todavía no se enviaron órdenes.").classes("text-sm text-gray-500")
            else:
                ui.table(
                    columns=[{"name": k, "label": label, "field": k, "align": "left" if k in ("created", "inst", "side", "type", "status") else "right"}
                             for k, label in [("created", "Fecha"), ("inst", "Instancia"), ("symbol", "Símbolo"), ("side", "Lado"),
                                              ("type", "Tipo"), ("qty", "Cantidad"), ("price", "Precio"), ("status", "Estado")]],
                    rows=[{"id": o.id, "created": _fmt_dt(o.created_at), "inst": state["names"].get(o.strategy_instance_id, "?"),
                           "symbol": o.symbol, "side": o.side, "type": o.order_type, "qty": o.qty, "price": _fmt_price(o.price),
                           "status": o.status} for o in state["orders"]],
                    row_key="id", pagination=15,
                ).props("flat dense").classes("w-full")

    async def load() -> None:
        instances = await load_instances()
        state["names"] = {i.id: i.name for i in instances}
        state["trades"] = await load_trades()
        state["orders"] = await load_orders()
        instance.set_options({ALL: "Todas las instancias", **state["names"]}, value=instance.value if instance.value in state["names"] else ALL)
        render()

    for control in (instance, status, side):
        control.on_value_change(lambda _: render())
    ui.timer(0.1, load, once=True)
    ui.timer(20, load)
