import logging

import plotly.graph_objects as go
from nicegui import ui

from app.config import settings
from app.exchange.bybit_client import bybit_client
from app.live.events import load_events
from app.live.orchestrator import orchestrator
from app.exports import instances_table, live_orders_table, live_trades_table
from app.timeutil import fmt, naive_local
from app.live.queries import load_bot_status, load_instances, load_orders, load_trades
from app.ui.export_button import export_button
from app.live.stats import closed_trades, summarize
from app.ui import backtest_charts as charts
from app.ui import backtest_widgets as w
from app.ui.backtest_format import fmt_pct, fmt_usd, sign_class
from app.ui.layout import page_content, pill, render_nav

logger = logging.getLogger(__name__)
REFRESH_SECONDS = 15

STATUS_SLOT = r"""
<q-td :props="props">
  <span :class="'pill pill-' + props.row.kind"><span class="dot"></span>{{ props.value }}</span>
</q-td>"""
SIDE_SLOT = r"""<q-td :props="props"><span :class="props.value === 'long' ? 'side-long' : 'side-short'">{{ props.value === 'long' ? '▲ Long' : '▼ Short' }}</span></q-td>"""
PNL_SLOT = r"""
<q-td :props="props" :class="props.row.pnl_raw > 0 ? 'text-[#0b7a3b]' : (props.row.pnl_raw < 0 ? 'text-[#d03b3b]' : '')">
  <span class="num">{{ props.value }}</span>
</q-td>"""


def _curve_figure(points: list, initial: float) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=[naive_local(t) for t, _ in points], y=[e for _, e in points], mode="lines+markers", line_shape="hv",
        line=dict(color=charts.BLUE, width=2), marker=dict(size=6, color=charts.BLUE),
        fill="tozeroy", fillcolor="rgba(42,120,214,0.08)", hovertemplate="$%{y:,.2f}<extra></extra>",
    ))
    fig.add_hline(y=initial, line=dict(color=charts.BASELINE, width=1, dash="dot"),
                  annotation_text="Capital inicial", annotation_font=dict(size=11, color=charts.MUTED), annotation_position="bottom right")
    lows = [e for _, e in points] + [initial]
    pad = (max(lows) - min(lows)) * 0.25 or initial * 0.01
    fig.update_layout(**charts._base_layout(height=280, margin=dict(l=64, r=16, t=12, b=36), showlegend=False))
    fig.update_xaxes(showgrid=False, showline=True, linecolor=charts.BASELINE, tickfont=dict(color=charts.MUTED))
    fig.update_yaxes(gridcolor=charts.GRID, zeroline=False, tickprefix="$", tickformat=",.2f", tickfont=dict(color=charts.MUTED),
                     range=[min(lows) - pad, max(lows) + pad])
    return fig


def _alert(kind: str, text: str, link: tuple[str, str] | None = None) -> None:
    icon, classes = {
        "bad": ("gpp_maybe", "bg-red-50 text-red-900"),
        "warn": ("warning_amber", "bg-amber-50 text-amber-900"),
        "info": ("info", "bg-blue-50 text-blue-900"),
    }[kind]
    with ui.row().classes(f"w-full items-center no-wrap gap-3 rounded-xl px-4 py-3 {classes}"):
        ui.icon(icon, size="22px")
        ui.label(text).classes("text-sm flex-1")
        if link:
            ui.link(link[0], link[1]).classes("text-sm font-semibold underline whitespace-nowrap")


def _table(columns: list[tuple[str, str]], rows: list[dict], left: tuple[str, ...] = (), slots: dict | None = None) -> None:
    table = ui.table(
        columns=[{"name": k, "label": label, "field": k, "align": "left" if k in left else "right"} for k, label in columns],
        rows=rows, row_key="id",
    ).props("flat dense hide-pagination").classes("w-full")
    for name, template in (slots or {}).items():
        table.add_slot(f"body-cell-{name}", template)


@ui.page("/")
def overview_page() -> None:
    render_nav("/")
    demo = settings.bybit_demo
    async def export_tables():
        instances, trades, orders = await load_instances(), await load_trades(), await load_orders(1000)
        names = {i.id: i.name for i in instances}
        by_instance: dict[int, list] = {}
        for trade in trades:
            by_instance.setdefault(trade.strategy_instance_id, []).append(trade)
        return [instances_table(instances, by_instance, summarize), live_trades_table(trades, names), live_orders_table(orders, names)]

    def header_actions() -> None:
        export_button(export_tables, "resumen", ["Instancias", "Trades", "Órdenes"])
        pill("Demo Trading" if demo else "MAINNET", "warn" if demo else "bad")

    with page_content("Resumen", "Estado de la cuenta y de las estrategias en vivo. Se actualiza solo.", actions=header_actions):
        body = ui.column().classes("w-full gap-6")

    async def refresh() -> None:
        instances = await load_instances()
        trades = await load_trades()
        status = await load_bot_status()
        try:
            wallet, wallet_error = await bybit_client.get_wallet_balance(), None
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo leer el balance: %s", exc)
            wallet, wallet_error = None, str(exc)

        stats = summarize(trades)
        by_instance: dict[int, list] = {}
        for trade in trades:
            by_instance.setdefault(trade.strategy_instance_id, []).append(trade)
        capital = sum(i.initial_capital for i in instances if i.is_active) or sum(i.initial_capital for i in instances)
        running_capital, curve = capital, []
        for trade in closed_trades(trades):
            running_capital += trade.pnl or 0.0
            curve.append((trade.closed_at, running_capital))
        names = {i.id: i.name for i in instances}

        problems = []
        for i in instances:
            if orchestrator.is_running(i.id):
                events = await load_events(i.id, 1)
                if events and events[0].kind in ("rejected", "error"):
                    problems.append((i, events[0]))

        body.clear()
        with body:
            if status["kill_switch"]:
                _alert("bad", "El kill-switch está activado: las estrategias no abren operaciones nuevas.", ("Desactivarlo", "/configuracion"))
            if wallet_error:
                _alert("warn", f"No se pudo leer el balance de Bybit: {wallet_error}")
            if instances and status["running"] == 0:
                _alert("info", "No hay instancias corriendo, el bot no está operando.", ("Ir a Estrategias", "/estrategias"))
            if not instances:
                _alert("info", "Todavía no creaste ninguna instancia de estrategia.", ("Crear la primera", "/estrategias"))
            for instance, event in problems:
                _alert("warn" if event.kind == "rejected" else "bad", f"{instance.name}: {event.message}", ("Ver actividad", "/estrategias"))

            with ui.element("div").classes("grid grid-cols-2 lg:grid-cols-4 gap-3 w-full"):
                w._tile("Balance de margen", fmt_usd(wallet["margin_balance"]) if wallet else "—",
                        sub=(f"Disponible {fmt_usd(wallet['available'])}" if wallet else None),
                        help_text="Lo que respalda las posiciones (USDT y USDC). El equity total, que incluye otras monedas de la cuenta "
                                  f"como el BTC y ETH de Demo, es {fmt_usd(wallet['equity']) if wallet else '—'}.")
                w._tile("PnL realizado", fmt_usd(stats["total_pnl"], signed=True), sub=f"{stats['closed']} trades cerrados",
                        color_class=sign_class(stats["total_pnl"]))
                w._tile("PnL de hoy", fmt_usd(stats["pnl_today"], signed=True), sub="Desde las 00:00, hora argentina", color_class=sign_class(stats["pnl_today"]))
                w._tile("Posiciones abiertas", str(stats["open"]),
                        sub=f"No realizado {fmt_usd(wallet['unrealised_pnl'], signed=True)}" if wallet else None)
            with ui.element("div").classes("grid grid-cols-3 gap-3 w-full"):
                w._tile("Win rate", fmt_pct(stats["win_rate_pct"]) if stats["win_rate_pct"] is not None else "—",
                        sub="% de trades ganadores")
                w._tile("Profit factor", f"{stats['profit_factor']:.2f}" if stats["profit_factor"] else "—",
                        sub="ganancias / pérdidas")
                w._tile("Instancias", f"{status['running']} / {len(instances)}", sub="corriendo / totales")

            with w.bordered_card():
                w.section_title("Capital realizado", "Capital inicial de las instancias activas más el PnL de los trades cerrados. No incluye la posición abierta.")
                if curve:
                    ui.plotly(_curve_figure(curve, capital)).classes("w-full")
                else:
                    w.empty_state("show_chart", "Todavía no hay trades cerrados. La curva aparece con el primero.")

            with w.bordered_card():
                w.section_title("Instancias")
                rows = []
                for i in instances:
                    s = summarize(by_instance.get(i.id, []))
                    if orchestrator.is_running(i.id):
                        label, kind = "Corriendo", "good"
                    elif i.is_active:
                        label, kind = "Sin runner", "warn"
                    else:
                        label, kind = "Apagada", "idle"
                    rows.append({
                        "id": i.id, "name": i.name, "strategy": i.strategy_key, "market": f"{i.symbol} · {i.timeframe}m",
                        "status": label, "kind": kind, "capital": fmt_usd(i.initial_capital),
                        "pnl": fmt_usd(s["total_pnl"], signed=True), "pnl_raw": s["total_pnl"], "closed": s["closed"], "open": s["open"],
                    })
                _table([("name", "Instancia"), ("strategy", "Estrategia"), ("market", "Mercado"), ("status", "Estado"),
                        ("capital", "Capital"), ("pnl", "PnL"), ("closed", "Cerrados"), ("open", "Abiertos")],
                       rows, left=("name", "strategy", "market", "status"), slots={"status": STATUS_SLOT, "pnl": PNL_SLOT})

            with w.bordered_card():
                w.section_title("Posiciones abiertas")
                open_trades = [t for t in trades if t.closed_at is None]
                if not open_trades:
                    ui.label("No hay posiciones abiertas.").classes("text-sm text-gray-500")
                else:
                    _table(
                        [("inst", "Instancia"), ("symbol", "Símbolo"), ("side", "Lado"), ("qty", "Cantidad"), ("entry", "Entrada"),
                         ("sl", "Stop"), ("tp", "Take profit"), ("since", "Desde")],
                        [{"id": t.id, "inst": names.get(t.strategy_instance_id, "?"), "symbol": t.symbol, "side": t.side,
                          "qty": t.qty, "entry": f"{t.entry_price:,.2f}", "sl": f"{t.stop_loss:,.2f}" if t.stop_loss else "—",
                          "tp": f"{t.take_profit:,.2f}" if t.take_profit else "—",
                          "since": fmt(t.opened_at, "%d/%m %H:%M")} for t in open_trades],
                        left=("inst", "symbol", "side"), slots={"side": SIDE_SLOT},
                    )

    ui.timer(0.1, refresh, once=True)
    ui.timer(REFRESH_SECONDS, refresh)
