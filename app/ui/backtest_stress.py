"""Pestaña Stress Test: sensibilidad a costos y escenarios de estres."""

import asyncio
import logging

from nicegui import ui

from app.backtest.config import ConfigError
from app.backtest.engine import DataError
from app.backtest.funding import get_funding_rates
from app.backtest.progress import Cancelled, Progress
from app.backtest.service import prepare_market_data
from app.backtest.stress import (
    CostSensitivity, StressResult, cost_sensitivity_sim_count, run_cost_sensitivity, run_stress_tests, stress_sim_count,
)
from app.exports import cost_tables, stress_table
from app.ui import backtest_charts as charts
from app.ui import backtest_widgets as w
from app.ui.backtest_config_panel import ConfigPanel
from app.ui.backtest_format import fmt_pct, fmt_usd, format_metric
from app.ui.export_button import export_button

logger = logging.getLogger(__name__)

RET_SLOT = r"""
<q-td :props="props" :class="props.value > 0 ? 'text-[#0b7a3b]' : (props.value < 0 ? 'text-[#d03b3b]' : '')">
  <span class="num">{{ props.value == null ? '—' : (props.value > 0 ? '+' : '') + props.value.toFixed(2) + '%' }}</span>
</q-td>"""
STATE_SLOT = r"""
<q-td :props="props">
  <span :class="'pill pill-' + props.row.kind"><span class="dot"></span>{{ props.value }}</span>
</q-td>"""


def _friendly(exc: Exception) -> str:
    if isinstance(exc, (ConfigError, DataError, ValueError)):
        return str(exc)
    return f"No se pudo completar el análisis: {exc}"


def _progress_widgets():
    """Barra de progreso y boton Cancelar. Devuelve (caja, barra, etiqueta, boton_cancelar)."""
    with ui.column().classes("w-full gap-1") as box:
        bar = ui.linear_progress(value=0, show_value=False).props("rounded size=8px")
        label = ui.label("").classes("text-xs text-gray-500")
    box.set_visibility(False)
    return box, bar, label


async def _prepare(panel: ConfigPanel):
    """Velas y funding historico (si hay); el funding se pide siempre porque los escenarios lo necesitan."""
    config, params = panel.collect(), panel.get_params()
    market = await prepare_market_data(config, panel.strategy_cls)
    if market.candles.empty:
        raise ValueError("No se encontraron velas para ese símbolo y rango.")
    funding = None
    try:
        funding = await get_funding_rates(config.symbol, market.start, market.end)
    except Exception:  # noqa: BLE001 - sin funding igual se puede analizar (se avisa)
        logger.warning("No se pudo obtener el funding historico para el analisis de estres", exc_info=True)
    return config, params, market, (None if funding is None or funding.empty else funding)


def _metric_row(label: str, m: dict | None, error: str | None = None) -> dict:
    m = m or {}
    return {
        "label": label, "ret": m.get("total_return_pct"), "sharpe": format_metric("sharpe_ratio", m.get("sharpe_ratio")) if m else "—",
        "dd": format_metric("max_drawdown_pct", m.get("max_drawdown_pct")) if m else "—",
        "pf": format_metric("profit_factor", m.get("profit_factor"), m) if m else "—",
        "final": fmt_usd(m.get("final_equity")) if m else "—", "trades": m.get("num_trades") if m else "—", "error": error or "",
    }


_COLS = [
    ("label", "Escenario", "left"), ("ret", "Retorno", "right"), ("sharpe", "Sharpe", "right"), ("dd", "Drawdown máx.", "right"),
    ("pf", "Profit factor", "right"), ("final", "Capital final", "right"), ("trades", "Operaciones", "right"),
]


def _metrics_table(rows: list[dict], row_key: str = "label") -> None:
    table = ui.table(
        columns=[{"name": k, "label": label, "field": k, "align": align} for k, label, align in _COLS],
        rows=rows, row_key=row_key,
    ).props("flat dense hide-pagination").classes("w-full")
    table.add_slot("body-cell-ret", RET_SLOT)


def build_stress_tab(panel: ConfigPanel) -> None:
    with ui.column().classes("w-full gap-5"):
        _build_costs(panel)
        _build_stress(panel)


# ------------------------------------------------------------------ sensibilidad a costos

def _build_costs(panel: ConfigPanel) -> None:
    state: dict = {"progress": None, "result": None}
    with w.bordered_card():
        w.section_title(
            "Sensibilidad a costos",
            "¿Cuánto de la ganancia sobrevive cuando los costos reales son peores que los del modelo? Se vuelve a correr la "
            "estrategia completa variando un costo por vez (slippage, comisiones, funding) y en cruce, y se estima en qué punto "
            "la estrategia deja de ganar.",
        )
        with ui.row().classes("w-full items-end justify-between gap-4"):
            estimate = ui.label("").classes("text-sm font-medium text-gray-700")
            with ui.row().classes("items-center gap-3"):
                cancel = ui.button("Cancelar", icon="stop").props("outline no-caps")
                run_button = ui.button("Analizar costos", icon="play_arrow").props("unelevated no-caps")
        cancel.set_visibility(False)
        box, bar, plabel = _progress_widgets()
        estimate.text = f"{cost_sensitivity_sim_count(True)} simulaciones completas. Con historias largas o estrategias lentas puede tardar varios minutos."
    results_box = ui.column().classes("w-full gap-4")
    with results_box:
        w.empty_state("payments", "Corré el análisis para ver cómo cambia el resultado con costos más altos.")

    def tick() -> None:
        p: Progress | None = state["progress"]
        if p is not None:
            bar.value = p.fraction
            plabel.text = f"{p.message} · {p.done} de {p.total} simulaciones"

    timer = ui.timer(0.4, tick, active=False)

    async def run() -> None:
        run_button.props("loading")
        cancel.set_visibility(True)
        box.set_visibility(True)
        bar.value = 0
        results_box.clear()
        with results_box:
            w.loading_state("Descargando velas y corriendo los escenarios de costos…")
        p = Progress(message="Preparando datos")
        state["progress"] = p
        timer.activate()
        try:
            config, params, market, funding = await _prepare(panel)
            result = await asyncio.to_thread(run_cost_sensitivity, panel.strategy_cls, params, config, market, funding, p)
            state["result"] = result
            results_box.clear()
            with results_box:
                _render_costs(result, config, market.quality.get("warnings", []))
        except Cancelled:
            results_box.clear()
            with results_box:
                w.notice("Análisis cancelado.", "info")
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, (ConfigError, DataError, ValueError)):
                logger.exception("Fallo la sensibilidad a costos")
            results_box.clear()
            with results_box:
                w.notice(_friendly(exc), "negative")
        finally:
            timer.deactivate()
            state["progress"] = None
            box.set_visibility(False)
            cancel.set_visibility(False)
            run_button.props(remove="loading")

    def do_cancel() -> None:
        if state["progress"] is not None:
            state["progress"].cancelled = True

    run_button.on_click(run)
    cancel.on_click(do_cancel)


def _point_rows(points) -> list[dict]:
    return [_metric_row(p.label, p.metrics, p.error) for p in points]


def _render_costs(result: CostSensitivity, config, data_warnings: list[str]) -> None:
    for text in data_warnings:
        w.notice(text, "warning")
    for text in result.notes:
        w.notice(text, "info")

    with ui.row().classes("w-full justify-end"):
        export_button(lambda: cost_tables(result), "sensibilidad_costos", ["Slippage", "Comisiones", "Funding", "Slippage x comisiones", "Punto de equilibrio"])

    slip_be, fee_be = result.break_even_slippage, result.break_even_fees
    with ui.element("div").classes("grid grid-cols-1 md:grid-cols-2 gap-4 w-full"):
        w._tile(
            "Slippage de equilibrio", f"{slip_be[0]:.1f} bps" if slip_be[0] is not None else "—", slip_be[1],
            "Slippage con el que el retorno total pasa de positivo a negativo, manteniendo el resto de la configuración.",
        )
        w._tile(
            "Comisiones de equilibrio", f"×{fee_be[0]:.2f} las actuales" if fee_be[0] is not None else "—", fee_be[1],
            "Cuántas veces habría que multiplicar las comisiones para que el retorno total pase a negativo.",
        )

    slip_values = [p.metrics["total_return_pct"] if p.metrics else None for p in result.slippage]
    fee_values = [p.metrics["total_return_pct"] if p.metrics else None for p in result.fees]
    with ui.element("div").classes("grid grid-cols-1 lg:grid-cols-2 gap-4 w-full"):
        with w.bordered_card():
            w.section_title("Slippage", "Retorno total según el slippage de cada orden (1 bps = 0,01%). Línea gris: 0%.")
            ui.plotly(charts.cost_line_chart(
                [p.x for p in result.slippage], slip_values, "Slippage (bps)", config.execution.slippage_bps, x_suffix=" bps",
            )).classes("w-full")
        with w.bordered_card():
            w.section_title("Comisiones", "Retorno total según cuántas veces las comisiones configuradas (taker y maker).")
            ui.plotly(charts.cost_line_chart(
                [p.x for p in result.fees], fee_values, "Comisiones (× las actuales)", 1.0, x_prefix="×",
            )).classes("w-full")

    with w.bordered_card():
        w.section_title("Funding", "Efecto del funding en el resultado. «Siempre en contra» hace que el funding sea un costo para cualquier posición.")
        _metrics_table(_point_rows(result.funding))

    with w.bordered_card():
        w.section_title(
            "Slippage × comisiones",
            "Retorno total (%) combinando los dos costos. Azul = gana, rojo = pierde, gris = 0%. Buscá cuánto margen hay antes de que el color cambie.",
        )
        base = (config.execution.slippage_bps, 1.0)
        ui.plotly(charts.sensitivity_heatmap(
            result.heat_x, result.heat_y, result.heat_z, "Slippage (bps)", "Comisiones (× las actuales)", "Retorno total (%)", 0.0, base,
        )).classes("w-full")

    with w.bordered_card():
        w.section_title("Detalle de cada punto", "Todas las simulaciones de las barridas de slippage y comisiones.")
        _metrics_table(_point_rows(result.slippage) + _point_rows(result.fees), row_key="label")


# ------------------------------------------------------------------ stress tests

def _build_stress(panel: ConfigPanel) -> None:
    state: dict = {"progress": None}
    with w.bordered_card():
        w.section_title(
            "Stress tests",
            "Escenarios fijos y nombrados donde la realidad es peor que el modelo: costos duplicados, stops que se ejecutan con gap, "
            "funding siempre en contra, un mercado más volátil y una combinación de todo. No se ordenan por rendimiento: sirven para ver "
            "qué tan frágil es la estrategia.",
        )
        with ui.row().classes("w-full items-end justify-between gap-4"):
            estimate = ui.label("").classes("text-sm font-medium text-gray-700")
            with ui.row().classes("items-center gap-3"):
                cancel = ui.button("Cancelar", icon="stop").props("outline no-caps")
                run_button = ui.button("Correr stress tests", icon="play_arrow").props("unelevated no-caps")
        cancel.set_visibility(False)
        box, bar, plabel = _progress_widgets()
        estimate.text = f"{stress_sim_count(panel.collect(), True)} escenarios."
    results_box = ui.column().classes("w-full gap-4")
    with results_box:
        w.empty_state("warning_amber", "Corré los stress tests para ver cómo se comporta la estrategia en condiciones adversas.")

    def tick() -> None:
        p: Progress | None = state["progress"]
        if p is not None:
            bar.value = p.fraction
            plabel.text = f"{p.message} · {p.done} de {p.total} escenarios"

    timer = ui.timer(0.4, tick, active=False)

    async def run() -> None:
        run_button.props("loading")
        cancel.set_visibility(True)
        box.set_visibility(True)
        bar.value = 0
        results_box.clear()
        with results_box:
            w.loading_state("Descargando velas y corriendo los escenarios…")
        p = Progress(message="Preparando datos")
        state["progress"] = p
        timer.activate()
        try:
            config, params, market, funding = await _prepare(panel)
            results = await asyncio.to_thread(run_stress_tests, panel.strategy_cls, params, config, market, funding, p)
            results_box.clear()
            with results_box:
                _render_stress(results, funding is not None, market.quality.get("warnings", []))
        except Cancelled:
            results_box.clear()
            with results_box:
                w.notice("Análisis cancelado.", "info")
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, (ConfigError, DataError, ValueError)):
                logger.exception("Fallaron los stress tests")
            results_box.clear()
            with results_box:
                w.notice(_friendly(exc), "negative")
        finally:
            timer.deactivate()
            state["progress"] = None
            box.set_visibility(False)
            cancel.set_visibility(False)
            run_button.props(remove="loading")

    def do_cancel() -> None:
        if state["progress"] is not None:
            state["progress"].cancelled = True

    run_button.on_click(run)
    cancel.on_click(do_cancel)


def _render_stress(results: list[StressResult], has_funding: bool, data_warnings: list[str]) -> None:
    for text in data_warnings:
        w.notice(text, "warning")
    base = next((r for r in results if r.key == "base"), None)
    base_ret = base.metrics["total_return_pct"] if base and base.metrics else None
    if not has_funding:
        w.notice("No hay funding histórico disponible: el escenario «Funding adverso» usa un funding constante de 0,03% cada 8 horas.", "info")
    if base_ret is None:
        w.notice("No se pudo simular el escenario base; los demás no se pueden comparar.", "negative")
    elif base_ret <= 0:
        w.notice("La estrategia ya pierde en el escenario base: los stress tests solo confirman que empeora. Revisá primero la estrategia.", "warning")

    scenarios = [r for r in results if r.key != "base"]
    losing = [r for r in scenarios if r.metrics and r.metrics["total_return_pct"] < 0]
    if base_ret is not None and base_ret > 0:
        worst = next((r for r in results if r.key == "worst" and r.metrics), None)
        if worst:
            w.notice(
                f"En el peor caso combinado, el retorno pasa de {fmt_pct(base_ret, signed=True)} a {fmt_pct(worst.metrics['total_return_pct'], signed=True)}. "
                f"{len(losing)} de {len(scenarios)} escenarios terminan en pérdida.",
                "warning" if losing else "positive",
            )

    with ui.row().classes("w-full justify-end"):
        export_button(lambda: [stress_table(results)], "stress_tests", ["Stress tests"])

    rows = []
    for r in results:
        m = r.metrics
        ret = m["total_return_pct"] if m else None
        row = _metric_row(r.label, m, r.error)
        row["desc"] = r.description
        row["delta"] = None if (base_ret is None or ret is None or r.key == "base") else ret - base_ret
        row["state"], row["kind"] = ("Error", "bad") if not m else (("Gana", "good") if ret > 0 else ("Pierde", "bad"))
        rows.append(row)

    with w.bordered_card():
        w.section_title("Resultado por escenario", "Pasá el mouse por el nombre para ver qué cambia en cada uno.")
        table = ui.table(
            columns=[
                {"name": "label", "label": "Escenario", "field": "label", "align": "left"},
                {"name": "ret", "label": "Retorno", "field": "ret", "align": "right"},
                {"name": "delta", "label": "Δ vs base (pp)", "field": "delta", "align": "right"},
                {"name": "dd", "label": "Drawdown máx.", "field": "dd", "align": "right"},
                {"name": "sharpe", "label": "Sharpe", "field": "sharpe", "align": "right"},
                {"name": "pf", "label": "Profit factor", "field": "pf", "align": "right"},
                {"name": "final", "label": "Capital final", "field": "final", "align": "right"},
                {"name": "trades", "label": "Operaciones", "field": "trades", "align": "right"},
                {"name": "state", "label": "Estado", "field": "state", "align": "left"},
            ],
            rows=rows, row_key="label",
        ).props("flat dense hide-pagination").classes("w-full")
        table.add_slot("body-cell-ret", RET_SLOT)
        table.add_slot("body-cell-delta", r"""
            <q-td :props="props" class="text-gray-600"><span class="num">{{ props.value == null ? '—' : (props.value > 0 ? '+' : '') + props.value.toFixed(2) }}</span></q-td>""")
        table.add_slot("body-cell-label", r"""
            <q-td :props="props"><span class="font-medium">{{ props.value }}</span><q-tooltip max-width="320px">{{ props.row.desc }}</q-tooltip></q-td>""")
        table.add_slot("body-cell-state", STATE_SLOT)

    with w.bordered_card():
        w.section_title("Retorno por escenario", "En el orden de la tabla, sin ranking. Línea punteada: el escenario base.")
        ui.plotly(charts.scenario_bars(
            [r.label for r in results], [r.metrics["total_return_pct"] if r.metrics else None for r in results], base_ret,
        )).classes("w-full")

    failed = [r for r in results if r.error]
    for r in failed:
        w.notice(f"«{r.label}» no se pudo simular: {r.error}", "negative")
