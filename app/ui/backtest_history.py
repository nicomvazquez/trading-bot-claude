"""Pestaña History: corridas guardadas con Ver, Clonar, Comparar, Descargar y Eliminar."""

import logging
from typing import Awaitable, Callable

import pandas as pd
from nicegui import ui
from sqlalchemy import delete, select
from sqlalchemy.orm import undefer

from app.backtest.config import BacktestConfig
from app.backtest.data import load_candles
from app.backtest.history import (
    RunView, config_diff, config_from_run, cost_summary, equity_pct, has_full_data, metric_rows, result_from_run, run_view,
)
from app.timeutil import fmt
from app.backtest.service import BacktestOutput, MarketData
from app.db.base import async_session
from app.db.models import BacktestRun
from app.exports import backtest_tables, runs_table
from app.strategies import registry
from app.ui import backtest_charts as charts
from app.ui import backtest_widgets as w
from app.ui.backtest_format import EXIT_REASON_LABELS, METRIC_GROUPS, METRIC_LABELS, TIMEFRAME_OPTIONS, format_metric
from app.ui.export_button import export_button

logger = logging.getLogger(__name__)

MAX_COMPARE = 6
RET_SLOT = r"""
<q-td :props="props" :class="props.value > 0 ? 'text-[#0b7a3b]' : (props.value < 0 ? 'text-[#d03b3b]' : '')">
  <span class="num">{{ props.value == null ? '—' : (props.value > 0 ? '+' : '') + props.value.toFixed(2) + '%' }}</span>
</q-td>"""


# ------------------------------------------------------------------ acceso a datos

async def load_runs(limit: int = 200) -> list[BacktestRun]:
    """Listado liviano: no trae operaciones ni curva de capital (columnas deferred)."""
    async with async_session() as session:
        result = await session.execute(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit))
        return list(result.scalars().all())


async def load_full(ids: list[int]) -> list[BacktestRun]:
    """Corridas con todo (operaciones y curva), en el orden pedido."""
    async with async_session() as session:
        result = await session.execute(
            select(BacktestRun).where(BacktestRun.id.in_(ids)).options(undefer(BacktestRun.trades), undefer(BacktestRun.equity))
        )
        found = {r.id: r for r in result.scalars().all()}
    return [found[i] for i in ids if i in found]


async def delete_runs(ids: list[int]) -> None:
    async with async_session() as session:
        await session.execute(delete(BacktestRun).where(BacktestRun.id.in_(ids)))
        await session.commit()


# ------------------------------------------------------------------ helpers de presentacion

def _strategy_name(key: str) -> str:
    try:
        return registry.get(key).display_name
    except KeyError:
        return key


def _market(run) -> str:
    return f"{run.symbol} · {TIMEFRAME_OPTIONS.get(run.timeframe, run.timeframe)}"


def _period(run) -> str:
    return f"{fmt(run.start_date, '%d/%m/%y')} → {fmt(run.end_date, '%d/%m/%y')}"


_VALUE_LABELS = {
    "execution.funding_mode": {"none": "Sin funding", "constant": "Constante", "historical": "Histórico real"},
    "execution.execution_model": {"next_open": "Apertura de la vela siguiente", "same_close": "Cierre de la misma vela"},
    "execution.order_type": {"market": "Market (taker)", "limit": "Limit (maker)"},
    "risk.sizing_mode": {"risk_based": "Por riesgo hasta el stop", "fixed_notional_pct": "Nocional fijo"},
    "validation.mc_method": {"shuffle": "Shuffle", "bootstrap": "Bootstrap", "block_bootstrap": "Block bootstrap"},
}


def _value(key: str, value) -> str:
    if value is None:
        return "—"
    if isinstance(value, str) and value in _VALUE_LABELS.get(key, {}):
        return _VALUE_LABELS[key][value]
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if key == "timeframe":
        return TIMEFRAME_OPTIONS.get(value, str(value))
    if key == "strategy_key":
        return _strategy_name(value)
    return f"{value:g}" if isinstance(value, float) else str(value)


async def rebuild_output(run: BacktestRun) -> BacktestOutput | None:
    """Reconstruye la salida de un backtest desde la fila guardada (las velas se traen de la cache)."""
    result = result_from_run(run)
    if result is None:
        return None
    config = config_from_run(run)
    try:
        candles, report = await load_candles(run.symbol, run.timeframe, run.start_date, run.end_date)
        quality = report.to_dict()
    except Exception:  # noqa: BLE001 - sin velas se ven igual metricas y curvas; solo falta el grafico de precio
        logger.warning("No se pudieron cargar las velas para ver la corrida %s", run.id, exc_info=True)
        candles, quality = pd.DataFrame(columns=["open", "high", "low", "close", "volume"]), {}
    market = MarketData(candles=candles, quality=quality, start=run.start_date, end=run.end_date)
    return BacktestOutput(result=result, metrics=run.metrics or {}, config=config, market=market, strategy_version=run.strategy_version or "")


# ------------------------------------------------------------------ pestaña

def build_history_tab(
    on_view: Callable[[BacktestOutput], None],
    on_clone: Callable[[BacktestConfig, str, dict], None],
) -> Callable[[], Awaitable[None]]:
    """Construye la pestaña dentro del contenedor actual y devuelve la funcion que la refresca."""
    state: dict = {"runs": [], "table": None}

    with w.bordered_card():
        with ui.row().classes("w-full items-start justify-between no-wrap gap-3"):
            w.section_title(
                "Corridas guardadas",
                "Cada backtest se guarda solo, con su configuración completa. Elegí una para verla o clonarla, o dos o más para compararlas.",
            )

            async def history_tables():
                return [runs_table(await load_runs(500), TIMEFRAME_OPTIONS)]

            export_button(history_tables, "historial_backtests", ["Backtests"], label="Descargar listado")
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            view_btn = ui.button("Ver", icon="visibility").props("outline dense no-caps")
            clone_btn = ui.button("Clonar configuración", icon="content_copy").props("outline dense no-caps")
            compare_btn = ui.button("Comparar", icon="compare_arrows").props("outline dense no-caps")

            async def one_run_tables():
                selected = _selected_ids()
                if len(selected) != 1:
                    raise ValueError("Elegí una sola corrida para descargar.")
                run = (await load_full(selected))[0]
                output = await rebuild_output(run)
                if output is None:
                    raise ValueError("Esta corrida se guardó antes de que se registraran las operaciones y la curva: no hay más datos para descargar.")
                return backtest_tables(
                    output.result, output.metrics, output.config.to_dict(), _strategy_name(run.strategy_key), output.strategy_version,
                    METRIC_GROUPS, METRIC_LABELS, EXIT_REASON_LABELS,
                )

            download_btn = export_button(one_run_tables, "backtest_guardado", ["Operaciones", "Equity", "Métricas"], label="Descargar corrida")
            delete_btn = ui.button("Eliminar", icon="delete").props("flat dense no-caps color=negative")
            hint = ui.label("").classes("text-xs text-gray-500")
        table_box = ui.column().classes("w-full")
    compare_box = ui.column().classes("w-full gap-4")

    def _selected_ids() -> list[int]:
        table = state["table"]
        return [row["id"] for row in table.selected] if table is not None else []

    def update_buttons() -> None:
        n = len(_selected_ids())
        view_btn.set_enabled(n == 1)
        clone_btn.set_enabled(n == 1)
        compare_btn.set_enabled(2 <= n <= MAX_COMPARE)
        download_btn.set_enabled(n == 1)
        delete_btn.set_enabled(n >= 1)
        if n == 0:
            hint.text = "Elegí filas de la tabla."
        elif n == 1:
            hint.text = "1 seleccionada: podés verla, clonarla o descargarla."
        elif n <= MAX_COMPARE:
            hint.text = f"{n} seleccionadas: podés compararlas o eliminarlas."
        else:
            hint.text = f"{n} seleccionadas: para comparar elegí como máximo {MAX_COMPARE}."

    def _run_by_id(run_id: int) -> BacktestRun | None:
        return next((r for r in state["runs"] if r.id == run_id), None)

    # ------------------------------------------------------------- acciones

    async def do_view() -> None:
        ids = _selected_ids()
        if len(ids) != 1:
            return
        run = (await load_full(ids))[0]
        output = await rebuild_output(run)
        if output is None:
            ui.notify("Esta corrida se guardó antes de que se registraran las operaciones y la curva. Podés clonar su configuración para volver a correrla.",
                      type="warning", timeout=8000)
            return
        on_view(output)
        ui.notify(f"Mostrando la corrida #{run.id}", type="positive")

    async def do_clone() -> None:
        ids = _selected_ids()
        if len(ids) != 1:
            return
        run = _run_by_id(ids[0])
        if run is None:
            return
        on_clone(config_from_run(run), run.strategy_key, run.params or {})
        note = "" if has_full_data(run) else " (corrida antigua: los costos y el riesgo se cargaron con valores por defecto)"
        ui.notify(f"Configuración de la corrida #{run.id} cargada arriba{note}.", type="positive", timeout=6000)

    async def do_delete() -> None:
        ids = _selected_ids()
        if not ids:
            return
        with ui.dialog() as dialog, ui.card().classes("gap-3 p-5 w-[420px] max-w-full"):
            ui.label("Eliminar corridas").classes("text-lg font-bold")
            ui.label(f"Se {'elimina la corrida' if len(ids) == 1 else f'eliminan las {len(ids)} corridas'} seleccionada{'s' if len(ids) > 1 else ''} del historial. No se puede deshacer.").classes("text-sm text-gray-600")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancelar", on_click=lambda: dialog.submit(False)).props("flat no-caps")
                ui.button("Eliminar", on_click=lambda: dialog.submit(True)).props("unelevated no-caps color=negative")
        confirmed = await dialog
        dialog.delete()
        if confirmed:
            await delete_runs(ids)
            compare_box.clear()
            await refresh()
            ui.notify("Corridas eliminadas", type="positive")

    async def do_compare() -> None:
        ids = _selected_ids()
        if not 2 <= len(ids) <= MAX_COMPARE:
            return
        runs = await load_full(ids)
        compare_box.clear()
        with compare_box:
            render_comparison(runs)
        ui.notify("Comparación armada debajo de la tabla", type="info")

    view_btn.on_click(do_view)
    clone_btn.on_click(do_clone)
    compare_btn.on_click(do_compare)
    delete_btn.on_click(do_delete)

    # ------------------------------------------------------------- tabla

    async def refresh() -> None:
        runs = await load_runs()
        state["runs"] = runs
        table_box.clear()
        with table_box:
            if not runs:
                w.empty_state("history", "Todavía no corriste ningún backtest.")
                state["table"] = None
            else:
                rows = []
                for r in runs:
                    m = r.metrics or {}
                    config = config_from_run(r)
                    rows.append({
                        "id": r.id, "fecha": fmt(r.created_at, "%d/%m/%y %H:%M"),
                        "estrategia": _strategy_name(r.strategy_key) + (f" ({r.strategy_version.split('+')[0]})" if r.strategy_version else ""),
                        "mercado": _market(r), "periodo": _period(r), "costos": cost_summary(config) if has_full_data(r) else "—",
                        "retorno": m.get("total_return_pct"), "sharpe": m.get("sharpe_ratio"), "dd": m.get("max_drawdown_pct"),
                        "trades": m.get("num_trades"), "completa": "Sí" if has_full_data(r) else "No",
                    })
                columns = [
                    {"name": "id", "label": "#", "field": "id", "align": "left", "sortable": True},
                    {"name": "fecha", "label": "Fecha", "field": "fecha", "align": "left"},
                    {"name": "estrategia", "label": "Estrategia", "field": "estrategia", "align": "left"},
                    {"name": "mercado", "label": "Mercado", "field": "mercado", "align": "left"},
                    {"name": "periodo", "label": "Período", "field": "periodo", "align": "left"},
                    {"name": "costos", "label": "Costos", "field": "costos", "align": "left"},
                    {"name": "retorno", "label": "Retorno", "field": "retorno", "align": "right", "sortable": True},
                    {"name": "sharpe", "label": "Sharpe", "field": "sharpe", "align": "right", "sortable": True},
                    {"name": "dd", "label": "Drawdown máx. (%)", "field": "dd", "align": "right", "sortable": True},
                    {"name": "trades", "label": "Operaciones", "field": "trades", "align": "right", "sortable": True},
                    {"name": "completa", "label": "Datos completos", "field": "completa", "align": "left"},
                ]
                table = ui.table(columns=columns, rows=rows, row_key="id", selection="multiple", pagination=10).props("flat dense").classes("w-full")
                table.add_slot("body-cell-retorno", RET_SLOT)
                table.on("selection", lambda _: update_buttons())
                state["table"] = table
        update_buttons()

    return refresh


# ------------------------------------------------------------------ comparacion

def render_comparison(runs: list[BacktestRun]) -> None:
    names = [f"#{r.id} · {_strategy_name(r.strategy_key)} · {_market(r)}" for r in runs]
    shorts = [f"#{r.id}" for r in runs]
    views: list[RunView] = [run_view(r, s) for r, s in zip(runs, shorts, strict=True)]

    with w.bordered_card():
        w.section_title(
            "Comparación de corridas",
            "En el orden en que las elegiste. No hay ranking automático: el color de cada corrida depende de su posición, no de su resultado.",
        )
        with ui.row().classes("gap-2 flex-wrap"):
            for color, name in zip(charts.SERIES_COLORS, names, strict=False):
                ui.html(f'<span style="display:inline-flex;align-items:center;gap:8px;padding:3px 12px;border:1px solid #e3e6ec;'
                        f'border-radius:999px;font-size:13px"><span style="width:10px;height:10px;border-radius:50%;background:{color}"></span>{name}</span>', sanitize=False)

    mismatches = []
    if len({r.symbol for r in runs}) > 1:
        mismatches.append("símbolos")
    if len({r.timeframe for r in runs}) > 1:
        mismatches.append("timeframes")
    if len({(r.end_date - r.start_date).days for r in runs}) > 1 or len({r.start_date.date() for r in runs}) > 1:
        mismatches.append("períodos")
    if mismatches:
        w.notice(
            "Estas corridas usan " + " y ".join(mismatches) + " distintos: las métricas y las curvas no son directamente comparables "
            "(otro mercado o otro tramo de historia puede explicar la diferencia por sí solo).", "warning",
        )

    diff = config_diff(views)
    with w.bordered_card():
        w.section_title("Qué cambió entre las corridas", "Solo se muestran los ajustes que difieren; el resto es igual en todas.")
        if not diff:
            ui.label("Las configuraciones son idénticas.").classes("text-sm text-gray-500")
        else:
            missing = [r.id for r in runs if not has_full_data(r)]
            if missing:
                w.notice("Las corridas " + ", ".join(f"#{i}" for i in missing) + " se guardaron antes de registrar la configuración completa: sus costos y riesgo figuran como «—».", "info")
            columns = [{"name": "label", "label": "Ajuste", "field": "label", "align": "left"}] + [
                {"name": f"v{i}", "label": s, "field": f"v{i}", "align": "left"} for i, s in enumerate(shorts)
            ]
            rows = [{"label": d["label"], **{f"v{i}": _value(d["key"], x) for i, x in enumerate(d["values"])}} for d in diff]
            ui.table(columns=columns, rows=rows, row_key="label").props("flat dense hide-pagination").classes("w-full")

    with w.bordered_card():
        w.section_title("Métricas lado a lado", "Todas sobre PnL neto de costos, según la configuración de cada corrida.")
        columns = [{"name": "grupo", "label": "", "field": "grupo", "align": "left"}, {"name": "label", "label": "Métrica", "field": "label", "align": "left"}] + [
            {"name": f"v{i}", "label": s, "field": f"v{i}", "align": "right"} for i, s in enumerate(shorts)
        ]
        rows = [
            {"grupo": m["group"], "label": m["label"], **{f"v{i}": format_metric(m["key"], val, views[i].metrics) for i, val in enumerate(m["values"])}}
            for m in metric_rows(views, METRIC_GROUPS, METRIC_LABELS)
        ]
        ui.table(columns=columns, rows=rows, row_key="label", pagination=0).props("flat dense hide-pagination").classes("w-full")

    series = []
    for r, name, short in zip(runs, names, shorts, strict=True):
        result = result_from_run(r)
        if result is not None:
            series.append((name, short, equity_pct(result.equity_curve, result.initial_capital)))
    with w.bordered_card():
        w.section_title("Curvas de capital", "Retorno acumulado (%) sobre el capital inicial de cada corrida, en una sola escala.")
        if len(series) < len(runs):
            w.notice("Alguna corrida se guardó sin su curva de capital y no aparece en el gráfico.", "info")
        if len(series) >= 2:
            ui.plotly(charts.compare_equity_chart(series)).classes("w-full")
        else:
            ui.label("Hacen falta al menos dos corridas con curva de capital guardada.").classes("text-sm text-gray-500")
