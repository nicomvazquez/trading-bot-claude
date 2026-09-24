import numpy as np
import pandas as pd
from nicegui import ui

from app.backtest.config import BacktestConfig
from app.backtest.engine import BacktestResult, TradeRecord
from app.backtest.metrics import trade_summary
from app.backtest.monte_carlo import MonteCarloResult
from app.ui import backtest_charts as charts
from app.ui.backtest_format import (
    EXIT_REASON_LABELS,
    METRIC_GROUPS,
    METRIC_HELP,
    METRIC_LABELS,
    NEG_CLASS,
    POS_CLASS,
    TIMEFRAME_OPTIONS,
    fmt_duration,
    fmt_pct,
    fmt_usd,
    format_metric,
    sign_class,
)

_NUM_OPTS = "{minimumFractionDigits: %d, maximumFractionDigits: %d}"


def bordered_card(classes: str = "") -> ui.card:
    return ui.card().props("flat bordered").classes(f"w-full p-4 gap-3 {classes}")


def section_title(title: str, subtitle: str | None = None) -> None:
    with ui.column().classes("gap-0"):
        ui.label(title).classes("text-base font-semibold text-gray-900")
        if subtitle:
            ui.label(subtitle).classes("text-sm text-gray-500")


def empty_state(icon: str, text: str) -> None:
    with ui.card().props("flat bordered").classes("w-full items-center p-10 gap-2 bg-transparent"):
        ui.icon(icon, size="40px").classes("text-gray-300")
        ui.label(text).classes("text-gray-500 text-center")


def loading_state(text: str) -> None:
    with ui.card().props("flat bordered").classes("w-full items-center p-10 gap-3 bg-transparent"):
        ui.spinner(size="lg")
        ui.label(text).classes("text-gray-500")


def notice(text: str, kind: str = "info") -> None:
    icon, classes = {
        "info": ("info", "bg-blue-50 text-blue-900"),
        "warning": ("warning", "bg-amber-50 text-amber-900"),
        "negative": ("error", "bg-red-50 text-red-900"),
        "positive": ("check_circle", "bg-green-50 text-green-900"),
    }[kind]
    with ui.row().classes(f"w-full items-start no-wrap gap-3 rounded-lg p-3 {classes}"):
        ui.icon(icon, size="20px")
        ui.label(text).classes("text-sm")


def _label_with_help(label: str, help_text: str | None) -> None:
    with ui.row().classes("items-center gap-1 no-wrap"):
        ui.label(label).classes("text-[11px] uppercase tracking-wider font-medium text-gray-500")
        if help_text:
            ui.icon("info", size="14px").classes("text-gray-400 cursor-help").tooltip(help_text)


def _tile(label: str, value: str, sub: str | None = None, help_text: str | None = None, color_class: str = "") -> None:
    with ui.card().props("flat bordered").classes("p-4 gap-1 justify-between"):
        _label_with_help(label, help_text)
        ui.label(value).classes(f"text-2xl font-semibold tracking-tight num {color_class}")
        if sub:
            ui.label(sub).classes("text-xs text-gray-500 num")


# --------------------------------------------------------------- warnings

def warnings_panel(warnings: list[dict]) -> None:
    """Advertencias metodologicas de la corrida (muestra chica, periodo corto, datos, costos...)."""
    if not warnings:
        notice("Sin advertencias metodológicas para esta corrida.", "positive")
        return
    has_warning = any(w["level"] == "warning" for w in warnings)
    label = f"Advertencias y notas ({len(warnings)})"
    with ui.expansion(label, icon="warning" if has_warning else "info", value=has_warning).classes(
        "w-full border border-gray-200 rounded-xl bg-white"
    ):
        with ui.column().classes("w-full gap-2 p-3"):
            for item in sorted(warnings, key=lambda w: w["level"] != "warning"):
                notice(item["text"], "warning" if item["level"] == "warning" else "info")


# ---------------------------------------------------------------- overview

def results_header(strategy_name: str, config: BacktestConfig, result: BacktestResult, strategy_version: str = "") -> None:
    curve = result.equity_curve
    period = f"{curve.index[0].strftime('%d/%m/%Y')} → {curve.index[-1].strftime('%d/%m/%Y')}"
    e, r = config.execution, config.risk
    funding = {"none": "sin funding", "constant": f"funding {e.funding_rate_pct:g}%/8h", "historical": "funding histórico"}[e.funding_mode]
    chips = [
        (strategy_name + (f" ({strategy_version})" if strategy_version else ""), "psychology"),
        (config.symbol, "currency_bitcoin"),
        (TIMEFRAME_OPTIONS.get(config.timeframe, config.timeframe), "schedule"),
        (period, "date_range"),
        (f"Capital {fmt_usd(config.initial_capital)}", "account_balance_wallet"),
        (f"Fees {e.taker_fee_pct:g}% / {e.effective_maker_fee_pct:g}%", "receipt_long"),
        (f"Slippage {e.slippage_bps:g} bps · spread {e.spread_bps:g} bps", "swap_vert"),
        (funding, "sync_alt"),
        (f"Apalancamiento máx. {r.max_leverage:g}x", "speed"),
    ]
    with ui.row().classes("w-full items-center justify-between gap-2"):
        ui.label("Resultados").classes("text-lg font-semibold")
        with ui.row().classes("gap-2 flex-wrap"):
            for text, icon in chips:
                ui.chip(text, icon=icon).props("outline dense color=grey-8")


def render_overview(metrics: dict, result: BacktestResult) -> None:
    """Solo las metricas principales: el detalle vive en las otras pestanas."""
    warnings_panel(metrics.get("warnings", []))

    n = metrics["num_trades"]
    closed = [t for t in result.trades if t.pnl is not None]
    wins = sum(1 for t in closed if t.pnl > 0)
    ret = metrics["total_return_pct"]
    color = sign_class(ret) or "text-gray-800"

    with ui.element("div").classes("grid grid-cols-2 md:grid-cols-4 gap-4 w-full"):
        with ui.card().props("flat bordered").classes("col-span-2 md:col-span-1 md:row-span-2 p-5 justify-between gap-6"):
            with ui.column().classes("gap-2"):
                _label_with_help("Retorno total", METRIC_HELP["total_return_pct"])
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.icon("trending_up" if ret >= 0 else "trending_down", size="40px").classes(color)
                    ui.label(fmt_pct(ret, signed=True)).classes(f"text-5xl font-semibold {color}")
            with ui.column().classes("gap-1"):
                ui.label(
                    f"{fmt_usd(metrics['final_equity'])} finales sobre {fmt_usd(metrics['initial_equity'])} iniciales"
                ).classes("text-sm text-gray-700")
                with ui.row().classes("items-center gap-1 no-wrap"):
                    cagr_note = "" if metrics.get("cagr_representative") else " (no representativo)"
                    ui.label(f"CAGR {format_metric('cagr_pct', metrics['cagr_pct'])}{cagr_note}").classes("text-sm text-gray-500")
                    ui.icon("info", size="14px").classes("text-gray-400 cursor-help").tooltip(METRIC_HELP["cagr_pct"])

        dd_sub = f"Duración: {format_metric('max_drawdown_duration_days', metrics['max_drawdown_duration_days'])}"
        if not metrics.get("max_drawdown_recovered", True):
            dd_sub += " · sin recuperar"
        _tile("Drawdown máx.", format_metric("max_drawdown_pct", metrics["max_drawdown_pct"]), dd_sub, METRIC_HELP["max_drawdown_pct"])
        _tile(
            "Sharpe", format_metric("sharpe_ratio", metrics["sharpe_ratio"]),
            f"Sortino {format_metric('sortino_ratio', metrics['sortino_ratio'])} · Calmar {format_metric('calmar_ratio', metrics['calmar_ratio'])}",
            METRIC_HELP["sharpe_ratio"],
        )
        _tile(
            "Profit factor", format_metric("profit_factor", metrics["profit_factor"], metrics),
            f"Prom. {format_metric('avg_win', metrics['avg_win'])} / {format_metric('avg_loss', metrics['avg_loss'])}",
            METRIC_HELP["profit_factor"],
        )
        _tile(
            "Win rate", format_metric("win_rate_pct", metrics["win_rate_pct"]),
            f"{wins} ganadoras · {len(closed) - wins} perdedoras", METRIC_HELP["win_rate_pct"],
        )
        _tile(
            "Expectativa por operación", format_metric("expectancy", metrics["expectancy"]),
            f"{n} operaciones en total", METRIC_HELP["expectancy"], color_class=sign_class(metrics["expectancy"]),
        )
        _tile(
            "Tiempo en mercado", format_metric("exposure_pct", metrics["exposure_pct"]),
            f"Long {format_metric('long_exposure_pct', metrics['long_exposure_pct'])} · Short {format_metric('short_exposure_pct', metrics['short_exposure_pct'])}",
            METRIC_HELP["exposure_pct"],
        )


# ------------------------------------------------------------------ equity

def trade_detail(trade: TradeRecord) -> None:
    side = "Long" if trade.side == "long" else "Short"
    items = [
        ("Operación", f"#{trade.id} · {side}"),
        ("Entrada", f"{trade.entry_time.strftime('%Y-%m-%d %H:%M')} a {trade.entry_price:,.2f}"),
        ("Salida", f"{trade.exit_time.strftime('%Y-%m-%d %H:%M')} a {trade.exit_price:,.2f}"),
        ("Duración", fmt_duration(trade.duration)),
        ("Tamaño", f"{trade.qty:,.6g} unidades · nocional {fmt_usd(trade.notional)}"),
        ("PnL bruto", fmt_usd(trade.gross_pnl, signed=True)),
        ("Comisiones", fmt_usd(-trade.fees, signed=True)),
        ("Funding", fmt_usd(trade.funding, signed=True)),
        ("PnL neto", fmt_usd(trade.pnl, signed=True)),
        ("Retorno s/ capital", fmt_pct((trade.pnl_pct or 0) * 100, signed=True)),
        ("Salida por", EXIT_REASON_LABELS.get(trade.exit_reason, trade.exit_reason)),
        ("Motivo de entrada", trade.reason or "—"),
        ("Stop loss", f"{trade.stop_loss:,.2f}" if trade.stop_loss else "—"),
        ("Take profit", f"{trade.take_profit:,.2f}" if trade.take_profit else "—"),
    ]
    with ui.element("div").classes("grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-3 w-full"):
        for label, value in items:
            with ui.column().classes("gap-0"):
                ui.label(label).classes("text-xs uppercase tracking-wide text-gray-500")
                cls = sign_class(trade.pnl) if label in ("PnL neto", "Retorno s/ capital") else ""
                ui.label(value).classes(f"text-sm font-medium {cls}")
    if trade.capped_by_leverage:
        notice("El tamaño de esta operación fue limitado por el apalancamiento máximo: su riesgo real fue menor al configurado.", "warning")
    if trade.open_at_end:
        notice("Posición cerrada a la fuerza al terminar los datos.", "info")


def render_equity_tab(result: BacktestResult, candles: pd.DataFrame) -> None:
    trades = {t.id: t for t in result.trades if t.pnl is not None}

    with bordered_card():
        section_title(
            "Equity y drawdown",
            "Equity = efectivo + PnL no realizado, al cierre de cada vela. Tocá un marcador para ver la operación.",
        )
        equity_plot = ui.plotly(charts.equity_drawdown_chart(result)).classes("w-full")

    with bordered_card():
        section_title("Operación seleccionada")
        detail_box = ui.column().classes("w-full gap-3")
        with detail_box:
            ui.label("Tocá un marcador de entrada o salida en cualquiera de los gráficos.").classes("text-sm text-gray-500")

    with bordered_card():
        section_title(
            "Precio y operaciones",
            "Dónde entró y salió cada operación sobre el precio de cierre. Punteado verde = ganadora, rojo = perdedora.",
        )
        price_plot = ui.plotly(charts.price_trades_chart(result, candles)).classes("w-full")

    def select(trade_id: int) -> None:
        trade = trades.get(trade_id)
        if trade is None:
            return
        detail_box.clear()
        with detail_box:
            trade_detail(trade)
        equity_plot.update_figure(charts.equity_drawdown_chart(result, highlight=trade))
        price_plot.update_figure(charts.price_trades_chart(result, candles, highlight=trade))

    def on_click(event) -> None:
        args = event.args if isinstance(event.args, dict) else {}
        for point in args.get("points", []):
            data = point.get("customdata")
            if isinstance(data, (list, tuple)):
                data = data[0] if data else None
            if isinstance(data, (int, float)) and int(data) in trades:
                select(int(data))
                return

    equity_plot.on("plotly_click", on_click)
    price_plot.on("plotly_click", on_click)


# ------------------------------------------------------------------ trades

_TRADE_FILTERS = {"all": "Todas", "long": "Long", "short": "Short", "win": "Ganadoras", "loss": "Perdedoras"}


def _filter_trades(closed: list[TradeRecord], mode: str) -> list[TradeRecord]:
    return {
        "all": closed,
        "long": [t for t in closed if t.side == "long"],
        "short": [t for t in closed if t.side == "short"],
        "win": [t for t in closed if t.pnl > 0],
        "loss": [t for t in closed if t.pnl < 0],
    }[mode]


def _number_slot(table: ui.table, column: str, decimals: int = 2, signed: bool = False, colored: bool = False,
                 prefix: str = "", suffix: str = "") -> None:
    fmt = _NUM_OPTS % (decimals, decimals)
    if signed:
        body = (
            f"(props.value > 0 ? '+' : (props.value < 0 ? '-' : '')) + '{prefix}' + "
            f"Math.abs(props.value).toLocaleString('en-US', {fmt}) + '{suffix}'"
        )
    else:
        body = f"'{prefix}' + Number(props.value).toLocaleString('en-US', {fmt}) + '{suffix}'"
    color_attr = (
        f''':class="props.value > 0 ? '{POS_CLASS}' : (props.value < 0 ? '{NEG_CLASS}' : '')" class="font-medium"'''
        if colored else ""
    )
    table.add_slot(
        f"body-cell-{column}",
        f"<q-td :props=\"props\" {color_attr}>{{{{ props.value == null ? '—' : {body} }}}}</q-td>",
    )


def _trade_rows(trades: list[TradeRecord]) -> list[dict]:
    return [
        {
            "id": t.id,
            "entrada": t.entry_time.strftime("%Y-%m-%d %H:%M"),
            "salida": t.exit_time.strftime("%Y-%m-%d %H:%M") if t.exit_time else "—",
            "lado": t.side,
            "px_in": round(t.entry_price, 4),
            "px_out": round(t.exit_price, 4) if t.exit_price is not None else None,
            "qty": t.qty,
            "notional": round(t.notional, 2),
            "gross": round(t.gross_pnl or 0.0, 2),
            "fees": round(-t.fees, 2),
            "funding": round(t.funding, 2),
            "net": round(t.pnl, 2),
            "ret": round((t.pnl_pct or 0) * 100, 3),
            "dur_h": round(t.duration.total_seconds() / 3600, 2) if t.duration is not None else None,
            "dur": fmt_duration(t.duration),
            "salida_por": EXIT_REASON_LABELS.get(t.exit_reason, t.exit_reason or "—"),
            "motivo": t.reason,
        }
        for t in trades
    ]


_TRADE_COLUMNS = [
    {"name": "id", "label": "ID", "field": "id", "align": "left", "sortable": True},
    {"name": "entrada", "label": "Entrada", "field": "entrada", "align": "left", "sortable": True},
    {"name": "salida", "label": "Salida", "field": "salida", "align": "left", "sortable": True},
    {"name": "lado", "label": "Lado", "field": "lado", "align": "left"},
    {"name": "px_in", "label": "Precio entrada", "field": "px_in", "align": "right", "sortable": True},
    {"name": "px_out", "label": "Precio salida", "field": "px_out", "align": "right", "sortable": True},
    {"name": "qty", "label": "Tamaño (unid.)", "field": "qty", "align": "right", "sortable": True},
    {"name": "notional", "label": "Nocional (USD)", "field": "notional", "align": "right", "sortable": True},
    {"name": "gross", "label": "PnL bruto", "field": "gross", "align": "right", "sortable": True},
    {"name": "fees", "label": "Comisiones", "field": "fees", "align": "right", "sortable": True},
    {"name": "funding", "label": "Funding", "field": "funding", "align": "right", "sortable": True},
    {"name": "net", "label": "PnL neto", "field": "net", "align": "right", "sortable": True},
    {"name": "ret", "label": "Retorno s/ capital", "field": "ret", "align": "right", "sortable": True},
    {"name": "dur_h", "label": "Duración", "field": "dur_h", "align": "right", "sortable": True},
    {"name": "salida_por", "label": "Salida por", "field": "salida_por", "align": "left"},
    {"name": "motivo", "label": "Motivo de entrada", "field": "motivo", "align": "left"},
]


def _side_comparison(closed: list[TradeRecord]) -> None:
    rows = []
    stats = {side: trade_summary([t for t in closed if t.side == side]) for side in ("long", "short")}
    spec = [
        ("Operaciones", lambda s: f"{s['num_trades']}"),
        ("Win rate", lambda s: fmt_pct(s["win_rate_pct"], 1)),
        ("PnL neto total", lambda s: fmt_usd(s["net_pnl"], signed=True)),
        ("PnL neto promedio", lambda s: fmt_usd(s["expectancy"], signed=True)),
        ("Profit factor", lambda s: "—" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}"),
        ("Ganancia promedio", lambda s: fmt_usd(s["avg_win"], signed=True)),
        ("Pérdida promedio", lambda s: fmt_usd(s["avg_loss"], signed=True)),
        ("Duración promedio", lambda s: format_metric("avg_trade_duration_hours", s["avg_trade_duration_hours"])),
    ]
    for label, fn in spec:
        rows.append({"metric": label, "long": fn(stats["long"]), "short": fn(stats["short"])})
    ui.table(
        columns=[
            {"name": "metric", "label": "", "field": "metric", "align": "left"},
            {"name": "long", "label": "Long", "field": "long", "align": "right"},
            {"name": "short", "label": "Short", "field": "short", "align": "right"},
        ],
        rows=rows, row_key="metric",
    ).props("flat dense hide-pagination").classes("w-full")


def _trade_analysis(subset: list[TradeRecord], label: str) -> None:
    if not subset:
        empty_state("insights", "No hay operaciones para este filtro.")
        return
    s = trade_summary(subset)
    pnls = [t.pnl for t in subset]
    hours = [t.duration.total_seconds() / 3600 for t in subset if t.duration is not None]

    with ui.element("div").classes("grid grid-cols-2 md:grid-cols-5 gap-4 w-full"):
        _tile("Ganancia promedio", fmt_usd(s["avg_win"], signed=True), None, METRIC_HELP["avg_win"], POS_CLASS if s["avg_win"] else "")
        _tile("Pérdida promedio", fmt_usd(s["avg_loss"], signed=True), None, METRIC_HELP["avg_loss"], NEG_CLASS if s["avg_loss"] else "")
        _tile("Payoff (gan./pérd.)", "—" if s["payoff_ratio"] is None else f"{s['payoff_ratio']:.2f}",
              "Ganancia promedio / pérdida promedio")
        _tile("Racha ganadora máx.", f"{s['max_consecutive_wins']}", "operaciones seguidas")
        _tile("Racha perdedora máx.", f"{s['max_consecutive_losses']}", "operaciones seguidas")

    with ui.element("div").classes("grid grid-cols-1 lg:grid-cols-2 gap-4 w-full"):
        with bordered_card():
            section_title(f"Distribución de PnL neto ({label})", "Línea: mediana. Cada barra agrupa operaciones con PnL parecido.")
            ui.plotly(charts.histogram(
                pnls, charts.BLUE, marks=[(float(np.median(pnls)), "Mediana", "top right")],
                x_prefix="$", x_suffix="", unit="operaciones", y_title="Operaciones",
            )).classes("w-full")
        with bordered_card():
            section_title(f"Distribución de duración ({label})", "Horas entre la entrada y la salida (resolución de una vela).")
            if hours:
                ui.plotly(charts.histogram(
                    hours, charts.BLUE_SOFT, marks=[(float(np.median(hours)), "Mediana", "top right")],
                    x_suffix=" h", unit="operaciones", y_title="Operaciones",
                )).classes("w-full")


def render_trades_tab(result: BacktestResult) -> None:
    closed = [t for t in result.trades if t.pnl is not None]
    if not closed:
        empty_state("list_alt", "La estrategia no cerró ninguna operación en este período.")
        return

    body = ui.column().classes("w-full gap-4")

    def draw(mode: str) -> None:
        body.clear()
        subset = _filter_trades(closed, mode)
        with body:
            with bordered_card():
                with ui.row().classes("w-full items-center justify-between gap-3"):
                    with ui.row().classes("gap-2"):
                        ui.chip(f"{len(subset)} operaciones", icon="list_alt").props("outline dense color=grey-8")
                        wins = sum(1 for t in subset if t.pnl > 0)
                        ui.chip(f"{wins} ganadoras", icon="arrow_upward").props("outline dense color=positive")
                        ui.chip(f"{sum(1 for t in subset if t.pnl < 0)} perdedoras", icon="arrow_downward").props("outline dense color=negative")
                        ui.chip(f"Neto {fmt_usd(sum(t.pnl for t in subset), signed=True)}", icon="payments").props("outline dense color=grey-8")
                    search = ui.input(placeholder="Buscar…").props("outlined dense clearable").classes("w-56")
                    with search.add_slot("prepend"):
                        ui.icon("search")
                if subset:
                    with ui.element("div").classes("w-full overflow-x-auto"):
                        table = ui.table(
                            columns=_TRADE_COLUMNS, rows=_trade_rows(subset), row_key="id", pagination=15,
                        ).props("flat dense").classes("w-full")
                    search.bind_value_to(table, "filter")
                    table.add_slot("body-cell-lado", """
                        <q-td :props="props">
                            <q-badge outline :color="props.value === 'long' ? 'blue-8' : 'orange-9'"
                                     :label="props.value === 'long' ? 'LONG' : 'SHORT'" />
                        </q-td>
                    """)
                    table.add_slot("body-cell-salida_por", """
                        <q-td :props="props">
                            <q-badge outline :color="({'Stop loss': 'red-7', 'Take profit': 'green-8'})[props.value] || 'grey-7'"
                                     :label="props.value" />
                        </q-td>
                    """)
                    table.add_slot("body-cell-dur_h", """
                        <q-td :props="props">{{ props.row.dur }}</q-td>
                    """)
                    for col in ("px_in", "px_out"):
                        _number_slot(table, col)
                    _number_slot(table, "qty", decimals=4)
                    _number_slot(table, "notional")
                    for col in ("gross", "fees", "funding", "net"):
                        _number_slot(table, col, signed=True, colored=col in ("gross", "net"), prefix="$")
                    _number_slot(table, "ret", decimals=2, signed=True, colored=True, suffix="%")
                else:
                    empty_state("filter_alt", "No hay operaciones para este filtro.")
            _trade_analysis(subset, _TRADE_FILTERS[mode].lower())

    ui.toggle(_TRADE_FILTERS, value="all", on_change=lambda e: draw(e.value)).props("no-caps unelevated")
    with bordered_card():
        section_title("Long vs Short", "Comparación de las operaciones de cada lado (sin filtro).")
        _side_comparison(closed)
    draw("all")


# ------------------------------------------------------------- risk / metrics

def _metric_group(title: str, keys: list[str], metrics: dict) -> None:
    with ui.card().props("flat bordered").classes("p-4 gap-2"):
        ui.label(title).classes("text-sm font-semibold")
        for i, key in enumerate(keys):
            if i:
                ui.separator()
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                with ui.row().classes("items-center gap-1 no-wrap"):
                    ui.label(METRIC_LABELS[key]).classes("text-sm text-gray-600")
                    if key in METRIC_HELP:
                        ui.icon("info", size="14px").classes("text-gray-400 cursor-help").tooltip(METRIC_HELP[key])
                ui.label(format_metric(key, metrics.get(key), metrics)).classes("text-sm font-medium").style(
                    "font-variant-numeric: tabular-nums"
                )


def render_risk_tab(metrics: dict) -> None:
    if not metrics.get("cagr_representative"):
        notice(
            f"El período simulado es de {metrics['period_days']:.0f} días. CAGR, Calmar y la volatilidad anualizada "
            "extrapolan a un año y no son representativos con menos de 365 días.", "warning",
        )
    with ui.element("div").classes("grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 w-full"):
        for title in ("Risk", "Performance", "Trades", "Exposure", "Costos"):
            _metric_group(title, METRIC_GROUPS[title], metrics)
    ui.label(
        "Todas las métricas se calculan sobre el equity marcado al cierre de cada vela (el drawdown intravela no se mide) "
        "y sobre PnL neto de comisiones, slippage y funding."
    ).classes("text-xs text-gray-500")


# ------------------------------------------------------------ monte carlo

def mc_intro() -> None:
    with ui.row().classes("w-full items-start no-wrap gap-3 rounded-lg p-3 bg-blue-50 text-blue-900"):
        ui.icon("info", size="20px")
        with ui.column().classes("gap-1"):
            ui.label(
                "Remuestrea los retornos netos de las operaciones de esta corrida miles de veces para estimar "
                "qué tan dependiente es el resultado de la suerte."
            ).classes("text-sm")
            ui.label("Shuffle: las mismas operaciones en otro orden. El retorno final NO cambia; solo varía el camino (drawdown).").classes("text-sm")
            ui.label("Bootstrap: sortea operaciones con reposición. Varía también el retorno final; supone operaciones independientes.").classes("text-sm")
            ui.label("Block bootstrap: como bootstrap pero copiando bloques consecutivos, conservando rachas de ganancias o pérdidas.").classes("text-sm")


def _metric_group_rows(title: str, rows: list[tuple[str, str]]) -> None:
    with ui.card().props("flat bordered").classes("p-4 gap-2"):
        ui.label(title).classes("text-sm font-semibold")
        for i, (label, value) in enumerate(rows):
            if i:
                ui.separator()
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label(label).classes("text-sm text-gray-600")
                ui.label(value).classes("text-sm font-medium").style("font-variant-numeric: tabular-nums")


_MC_METHOD_LABELS = {"shuffle": "Shuffle de operaciones", "bootstrap": "Bootstrap", "block_bootstrap": "Block bootstrap"}


def render_mc_results(mc: MonteCarloResult) -> None:
    shuffle = mc.method == "shuffle"

    chips = [
        (f"Método: {_MC_METHOD_LABELS[mc.method]}", "casino"),
        (f"{mc.n_sims:,} simulaciones", "repeat"),
        (f"Semilla {mc.seed}", "tag"),
        (f"{mc.n_trades} operaciones", "list_alt"),
    ]
    if mc.block_size:
        chips.append((f"Bloques de {mc.block_size}", "view_module"))
    with ui.row().classes("gap-2 flex-wrap"):
        for text, icon in chips:
            ui.chip(text, icon=icon).props("outline dense color=grey-8")

    for text in mc.warnings:
        notice(text, "warning" if "poco confiable" in text or "dependencia" in text else "info")

    with ui.element("div").classes("grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 w-full"):
        if shuffle:
            _metric_group_rows("Retorno final", [
                ("Todas las simulaciones", fmt_pct(mc.return_pct_p50, signed=True)),
                ("Es el mismo valor", "el orden no lo cambia"),
            ])
        else:
            _metric_group_rows("Retorno final", [
                ("P5 (pesimista)", fmt_pct(mc.return_pct_p5, signed=True)),
                ("P25", fmt_pct(mc.return_pct_p25, signed=True)),
                ("Mediana", fmt_pct(mc.return_pct_p50, signed=True)),
                ("P75", fmt_pct(mc.return_pct_p75, signed=True)),
                ("P95 (optimista)", fmt_pct(mc.return_pct_p95, signed=True)),
            ])
        _metric_group_rows("Drawdown máximo", [
            ("P5 (peor caso)", fmt_pct(mc.max_dd_p5)),
            ("P50 (mediana)", fmt_pct(mc.max_dd_p50)),
            ("P95 (mejor caso)", fmt_pct(mc.max_dd_p95)),
        ])
        _metric_group_rows("Riesgo", [
            ("Probabilidad de pérdida", fmt_pct(mc.prob_loss_pct, digits=1)),
            (f"Probabilidad de drawdown > {mc.ruin_threshold_pct:.0f}%", fmt_pct(mc.prob_ruin_pct, digits=1)),
        ])
        _metric_group_rows("Resultado real", [
            ("Retorno real", fmt_pct(mc.actual_return_pct, signed=True)),
            ("Drawdown real", fmt_pct(mc.actual_max_dd_pct)),
            ("Drawdown real peor que", f"{mc.actual_dd_worse_than_pct:.0f}% de las simulaciones"),
        ])

    with ui.element("div").classes(f"grid grid-cols-1 {'' if shuffle else 'lg:grid-cols-2'} gap-4 w-full"):
        with bordered_card():
            section_title(
                "Distribución del drawdown máximo",
                f"{mc.n_sims:,} simulaciones. Líneas: peor caso (P5), mediana y el drawdown real.",
            )
            ui.plotly(charts.histogram(
                mc.drawdowns_distribution, charts.RED,
                marks=[(mc.max_dd_p5, "P5", "top left"), (mc.max_dd_p50, "Mediana", "top right"),
                       (mc.actual_max_dd_pct, "Real", "bottom right")],
            )).classes("w-full")
        if not shuffle:
            with bordered_card():
                section_title(
                    "Distribución del retorno final",
                    f"{mc.n_sims:,} simulaciones. Líneas: pesimista (P5), mediana y el retorno real.",
                )
                ui.plotly(charts.histogram(
                    mc.returns_distribution, charts.BLUE,
                    marks=[(mc.return_pct_p5, "P5", "top left"), (mc.return_pct_p50, "Mediana", "top right"),
                           (mc.actual_return_pct, "Real", "bottom right")],
                )).classes("w-full")


# ---------------------------------------------------------------- history

def render_runs_table(runs: list) -> None:
    if not runs:
        empty_state("history", "Todavía no corriste ningún backtest.")
        return
    rows = []
    for i, run in enumerate(runs):
        m = run.metrics or {}
        rows.append({
            "id": i,
            "fecha": run.created_at.strftime("%Y-%m-%d %H:%M"),
            "estrategia": run.strategy_key,
            "mercado": f"{run.symbol} · {TIMEFRAME_OPTIONS.get(run.timeframe, run.timeframe)}",
            "retorno": m.get("total_return_pct"),
            "sharpe": m.get("sharpe_ratio"),
            "dd": m.get("max_drawdown_pct"),
            "trades": m.get("num_trades"),
            "params": ", ".join(f"{k}={v}" for k, v in (run.params or {}).items()),
        })
    columns = [
        {"name": "fecha", "label": "Fecha", "field": "fecha", "align": "left", "sortable": True},
        {"name": "estrategia", "label": "Estrategia", "field": "estrategia", "align": "left"},
        {"name": "mercado", "label": "Mercado", "field": "mercado", "align": "left"},
        {"name": "retorno", "label": "Retorno (%)", "field": "retorno", "align": "right", "sortable": True},
        {"name": "sharpe", "label": "Sharpe", "field": "sharpe", "align": "right", "sortable": True},
        {"name": "dd", "label": "Drawdown máx. (%)", "field": "dd", "align": "right", "sortable": True},
        {"name": "trades", "label": "Operaciones", "field": "trades", "align": "right", "sortable": True},
        {"name": "params", "label": "Parámetros", "field": "params", "align": "left"},
    ]
    table = ui.table(columns=columns, rows=rows, row_key="id", pagination=10).props("flat dense").classes("w-full")
    table.add_slot("body-cell-retorno", """
        <q-td :props="props" class="font-medium" :class="props.value > 0 ? '%s' : (props.value < 0 ? '%s' : '')">
            {{ props.value == null ? '—' : (props.value > 0 ? '+' : '') + props.value.toFixed(2) + '%%' }}
        </q-td>
    """ % (POS_CLASS, NEG_CLASS))
    table.add_slot("body-cell-params", """
        <q-td :props="props" style="max-width: 320px">
            <div class="ellipsis text-gray-600">{{ props.value }}<q-tooltip>{{ props.value }}</q-tooltip></div>
        </q-td>
    """)
