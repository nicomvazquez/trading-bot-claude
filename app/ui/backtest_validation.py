"""Pestaña Validation: fuera de muestra (in-sample / out-of-sample) y walk-forward."""

import asyncio
import datetime as dt
import logging
import math

import pandas as pd
from nicegui import ui
from pydantic import ValidationError

from app.backtest.config import ConfigError
from app.backtest.engine import DataError
from app.backtest.progress import Progress
from app.backtest.service import prepare_market_data
from app.backtest.validation import (
    MAX_WF_SIMULATIONS,
    OBJECTIVES,
    OptimizeSpec,
    Segment,
    WalkForwardResult,
    run_in_out_sample,
    run_walk_forward,
    split_from_fraction,
)
from app.ui import backtest_charts as charts
from app.ui import backtest_widgets as w
from app.ui.backtest_axes import AxesSelector
from app.ui.backtest_config_panel import ConfigPanel
from app.ui.backtest_format import METRIC_LABELS, NEG_CLASS, fmt_pct, format_metric, sign_class

logger = logging.getLogger(__name__)

_COMPARED_METRICS = [
    "total_return_pct", "sharpe_ratio", "sortino_ratio", "max_drawdown_pct", "profit_factor",
    "win_rate_pct", "expectancy", "num_trades", "exposure_pct",
]


def _friendly(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "Parámetros de la estrategia inválidos: " + "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
        )
    if isinstance(exc, (ConfigError, DataError, ValueError)):
        return str(exc)
    return f"No se pudo completar el análisis: {exc}"


def _outlined(element):
    return element.props("outlined dense")


def build_validation_tab(panel: ConfigPanel) -> None:
    with ui.column().classes("w-full gap-5"):
        _build_oos(panel)
        _build_walk_forward(panel)


# ------------------------------------------------------------ out of sample

def _build_oos(panel: ConfigPanel) -> None:
    with w.bordered_card():
        w.section_title(
            "Fuera de muestra (in-sample / out-of-sample)",
            "Cortás el período en dos y aplicás los mismos parámetros a cada tramo. El out-of-sample arranca de cero con el "
            "capital inicial, usando la historia anterior solo para calentar indicadores.",
        )
        with ui.row().classes("w-full items-start gap-4"):
            pct = _outlined(ui.number("In-sample (%)", value=70, min=10, max=90, step=5)).classes("w-40")
            cut_date = _outlined(ui.input("Fecha de corte (AAAA-MM-DD)")).classes("w-64").props(
                'hint="Opcional: si la completás, reemplaza al porcentaje"'
            )
            button = ui.button("Correr validación fuera de muestra", icon="play_arrow").props("unelevated no-caps")
        w.notice(
            "Si ajustaste los parámetros mirando el resultado del tramo out-of-sample, ese tramo deja de ser fuera de muestra: "
            "elegí los parámetros con el in-sample y usá el out-of-sample una sola vez para confirmar.", "info",
        )
    results_box = ui.column().classes("w-full gap-4")

    async def run() -> None:
        button.props("loading")
        results_box.clear()
        with results_box:
            w.loading_state("Descargando velas y simulando los dos tramos…")
        try:
            config, params = panel.collect(), panel.get_params()
            market = await prepare_market_data(config, panel.strategy_cls)
            if market.candles.empty:
                raise ValueError("No se encontraron velas para ese símbolo y rango.")
            if (cut_date.value or "").strip():
                try:
                    cut = pd.Timestamp(dt.datetime.strptime(cut_date.value.strip(), "%Y-%m-%d"), tz="UTC")
                except ValueError as exc:
                    raise ValueError("Fecha de corte inválida: usá el formato AAAA-MM-DD.") from exc
            else:
                cut = split_from_fraction(market.candles, float(pct.value or 70) / 100)
            in_seg, out_seg = await asyncio.to_thread(
                run_in_out_sample, panel.strategy_cls, params, config, market, cut
            )
            results_box.clear()
            with results_box:
                _render_oos(in_seg, out_seg, out_seg.start, market.quality.get("warnings", []))
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, (ConfigError, DataError, ValidationError, ValueError)):
                logger.exception("Fallo la validacion fuera de muestra")
            results_box.clear()
            with results_box:
                w.notice(_friendly(exc), "negative")
        finally:
            button.props(remove="loading")

    button.on_click(run)


def _period_chip(seg: Segment) -> str:
    days = (seg.end - seg.start).total_seconds() / 86400
    return f"{seg.label}: {seg.start.strftime('%d/%m/%Y')} → {seg.end.strftime('%d/%m/%Y')} ({days:.0f} días)"


def _render_oos(in_seg: Segment, out_seg: Segment, cut, data_warnings: list[str]) -> None:
    for text in data_warnings:
        w.notice(text, "warning")
    with ui.row().classes("gap-2 flex-wrap"):
        for seg in (in_seg, out_seg):
            ui.chip(_period_chip(seg), icon="date_range").props("outline dense color=grey-8")

    m_in, m_out = in_seg.metrics, out_seg.metrics
    if "error" in m_in or "error" in m_out:
        w.notice("Alguno de los tramos no tiene suficientes velas para simular.", "warning")
        return

    # degradacion entre tramos
    if (m_in["total_return_pct"] or 0) > 0 and (m_out["total_return_pct"] or 0) < 0:
        w.notice("La estrategia gana en el in-sample pero pierde en el out-of-sample: señal clásica de sobreajuste.", "warning")
    s_in, s_out = m_in.get("sharpe_ratio"), m_out.get("sharpe_ratio")
    if s_in and s_in > 0 and s_out is not None and s_out < 0.5 * s_in:
        w.notice(
            f"El Sharpe fuera de muestra ({s_out:.2f}) es menos de la mitad del in-sample ({s_in:.2f}): "
            "posible sobreajuste, o simplemente un tramo distinto del mercado.", "warning",
        )
    for label, metrics in (("In-sample", m_in), ("Out-of-sample", m_out)):
        for item in metrics.get("warnings", []):
            if item["level"] == "warning" and "Sin slippage" not in item["text"]:
                w.notice(f"{label}: {item['text']}", "warning")

    rows = []
    for key in _COMPARED_METRICS:
        rows.append({
            "metric": METRIC_LABELS[key],
            "in": format_metric(key, m_in.get(key), m_in),
            "out": format_metric(key, m_out.get(key), m_out),
        })
    rows.append({"metric": "Duración (días)", "in": f"{m_in['period_days']:.0f}", "out": f"{m_out['period_days']:.0f}"})
    with w.bordered_card():
        w.section_title(
            "Métricas lado a lado",
            "Los retornos no están escalados por duración: los tramos pueden tener largos distintos.",
        )
        ui.table(
            columns=[
                {"name": "metric", "label": "", "field": "metric", "align": "left"},
                {"name": "in", "label": "In-sample", "field": "in", "align": "right"},
                {"name": "out", "label": "Out-of-sample", "field": "out", "align": "right"},
            ],
            rows=rows, row_key="metric",
        ).props("flat dense hide-pagination").classes("w-full")

    with w.bordered_card():
        w.section_title(
            "Capital en los dos tramos",
            "El out-of-sample se dibuja continuando el capital final del in-sample solo para leer las dos curvas juntas.",
        )
        ui.plotly(charts.oos_equity_chart(in_seg.result, out_seg.result, cut)).classes("w-full")


# -------------------------------------------------------------- walk-forward

def _estimate_windows(days: float, train: float, test: float, step: float) -> int:
    days -= 1  # la historia real cubre algo menos que los dias pedidos (vela en formacion, alineacion)
    if min(train or 0, test or 0, step or 0) <= 0 or days < train + test:
        return 0
    return int(math.floor((days - train - test) / step)) + 1


def _build_walk_forward(panel: ConfigPanel) -> None:
    holder: dict = {}

    with w.bordered_card():
        w.section_title(
            "Walk-forward",
            "Desliza una ventana de entrenamiento seguida de una de test a lo largo de la historia y mide cada test por separado. "
            "Sirve para ver si el comportamiento se sostiene en el tiempo.",
        )
        with ui.element("div").classes("grid grid-cols-2 md:grid-cols-4 gap-4 w-full items-center"):
            train = _outlined(ui.number("Entrenamiento (días)", value=45, min=5, step=5)).classes("w-full")
            test = _outlined(ui.number("Test (días)", value=15, min=2, step=1)).classes("w-full")
            step = _outlined(ui.number("Paso (días)", value=15, min=1, step=1)).classes("w-full")
            mode = ui.toggle({"fixed": "Parámetros fijos", "reopt": "Re-optimizar en cada ventana"}, value="fixed").props(
                "no-caps unelevated"
            ).classes("col-span-2 md:col-span-1")

        opt_box = ui.column().classes("w-full gap-3")
        with opt_box:
            w.notice(
                "En cada ventana se elige el mejor conjunto de parámetros usando SOLO el entrenamiento y se evalúa una única vez "
                "en el test siguiente. El objetivo lo elegís vos: cuidado con optimizar Sharpe cuando hay pocas operaciones.",
                "info",
            )
            selector = AxesSelector(panel, allow_second=True, on_change=lambda: update_estimate() if "label" in holder else None)
            with ui.element("div").classes("grid grid-cols-2 md:grid-cols-4 gap-4 w-full items-center"):
                objective = _outlined(ui.select(
                    {k: METRIC_LABELS[k] for k in OBJECTIVES}, label="Objetivo de la optimización", value="sharpe_ratio",
                )).classes("w-full col-span-2")
                min_trades = _outlined(ui.number("Mín. operaciones en el entrenamiento", value=10, min=0, step=1)).classes("w-full col-span-2")
        opt_box.bind_visibility_from(mode, "value", backward=lambda v: v == "reopt")

        with ui.row().classes("w-full items-end justify-between gap-4"):
            holder["label"] = ui.label("").classes("text-sm font-medium text-gray-700")
            with ui.row().classes("items-center gap-3"):
                holder["cancel"] = ui.button("Cancelar", icon="stop").props("outline no-caps")
                holder["run"] = ui.button("Correr walk-forward", icon="play_arrow").props("unelevated no-caps")
        holder["cancel"].set_visibility(False)
        with ui.column().classes("w-full gap-1") as progress_box:
            bar = ui.linear_progress(value=0, show_value=False).props("rounded size=8px")
            progress_label = ui.label("").classes("text-xs text-gray-500")
        progress_box.set_visibility(False)

    def update_estimate() -> None:
        try:
            days = float(panel.days.value or 0)
            n = _estimate_windows(days, float(train.value or 0), float(test.value or 0), float(step.value or 0))
        except (TypeError, ValueError):
            n = 0
        combos = selector.count()[0] if mode.value == "reopt" else 1
        sims = n * (combos + 1)
        too_many = sims > MAX_WF_SIMULATIONS or n == 0
        need = (train.value or 0) + (test.value or 0)
        text = f"{n} ventanas · {sims:,} simulaciones (máximo {MAX_WF_SIMULATIONS:,})"
        if n == 0:
            text = f"No entra ninguna ventana: hacen falta al menos {need:g} días de historia (hoy: {panel.days.value:g})."
        holder["label"].text = text
        holder["label"].classes(replace=AxesSelector.label_classes(too_many))
        holder["run"].set_enabled(not too_many)

    for element in (train, test, step, mode, panel.days):
        element.on_value_change(lambda _: update_estimate())
    update_estimate()

    results_box = ui.column().classes("w-full gap-4")
    with results_box:
        w.empty_state("timeline", "Configurá las ventanas y corré el walk-forward.")
    progress = {"obj": None}

    def tick() -> None:
        p: Progress | None = progress["obj"]
        if p is None:
            return
        bar.value = p.fraction
        progress_label.text = f"{p.message} · {p.done:,} de {p.total:,} simulaciones"

    timer = ui.timer(0.4, tick, active=False)

    async def run() -> None:
        holder["run"].props("loading")
        holder["cancel"].set_visibility(True)
        progress_box.set_visibility(True)
        bar.value = 0
        results_box.clear()
        with results_box:
            w.loading_state("Descargando velas y corriendo las ventanas…")
        p = Progress(message="Preparando datos")
        progress["obj"] = p
        timer.activate()
        try:
            config, params = panel.collect(), panel.get_params()
            market = await prepare_market_data(config, panel.strategy_cls)
            if market.candles.empty:
                raise ValueError("No se encontraron velas para ese símbolo y rango.")
            spec = None
            if mode.value == "reopt":
                spec = OptimizeSpec(selector.axes(), objective.value, int(min_trades.value or 0))
            result = await asyncio.to_thread(
                run_walk_forward, panel.strategy_cls, params, config, market,
                float(train.value), float(test.value), float(step.value), spec, p,
            )
            results_box.clear()
            with results_box:
                _render_walk_forward(result, config.timeframe)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, (ConfigError, DataError, ValidationError, ValueError)):
                logger.exception("Fallo el walk-forward")
            results_box.clear()
            with results_box:
                w.notice(_friendly(exc), "negative")
        finally:
            timer.deactivate()
            progress["obj"] = None
            progress_box.set_visibility(False)
            holder["cancel"].set_visibility(False)
            holder["run"].props(remove="loading")

    def cancel() -> None:
        if progress["obj"] is not None:
            progress["obj"].cancelled = True

    holder["run"].on_click(run)
    holder["cancel"].on_click(cancel)


def _render_walk_forward(result: WalkForwardResult, timeframe: str) -> None:
    agg = result.aggregate
    for text in result.warnings:
        w.notice(text, "warning" if ("solapan" in text or "poco confiables" in text or "cancel" in text) else "info")
    if not result.windows:
        return

    with ui.element("div").classes("grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 w-full"):
        w._tile("Ventanas evaluadas", f"{agg['n_evaluated']} / {agg['n_windows']}",
                f"{agg['n_skipped']} omitidas" if agg["n_skipped"] else "Todas con operaciones",
                "Ventanas de test que se pudieron evaluar.")
        w._tile("Ventanas ganadoras", fmt_pct(agg["pct_profitable"], 0), "con retorno de test > 0",
                "Porcentaje de ventanas de test que terminaron en ganancia.")
        w._tile("Retorno de test (mediana)", fmt_pct(agg["return_median"], 2, signed=True),
                f"{fmt_pct(agg['return_min'], 1, signed=True)} a {fmt_pct(agg['return_max'], 1, signed=True)}",
                "Mediana y rango de los retornos de las ventanas de test.", color_class=sign_class(agg["return_median"]))
        w._tile("Sharpe de test (mediana)", format_metric("sharpe_ratio", agg["sharpe_median"]), None,
                "Mediana del Sharpe de las ventanas de test.")
        w._tile("Peor drawdown de test", fmt_pct(agg["worst_drawdown"]), None, "El peor drawdown entre las ventanas de test.")
        if "compounded_return_pct" in agg:
            w._tile("Retorno compuesto OOS", fmt_pct(agg["compounded_return_pct"], 2, signed=True),
                    f"{agg['trades_total']} operaciones", "Retorno de encadenar todas las ventanas de test consecutivas.",
                    color_class=sign_class(agg["compounded_return_pct"]))
        else:
            w._tile("Operaciones (suma)", f"{agg['trades_total']}", "cuenta doble por el solapamiento", "Suma de operaciones de todas las ventanas.")
    if "distinct_param_sets" in agg:
        w.notice(
            f"Parámetros elegidos: {agg['distinct_param_sets']} conjuntos distintos en {agg['n_evaluated']} ventanas; "
            f"el más frecuente se eligió en el {agg['most_common_share']:.0f}% de ellas. "
            "Mucha variación entre ventanas indica que la estrategia no es estable frente a esos parámetros.", "info",
        )

    evaluated = [wr for wr in result.windows]
    labels = [f"V{wr.window.index + 1}" for wr in evaluated]
    hover = [f"{wr.window.test_start.strftime('%d/%m/%y')} → {wr.window.test_end.strftime('%d/%m/%y')}" for wr in evaluated]
    train_r = [(wr.train_metrics or {}).get("total_return_pct") for wr in evaluated]
    test_r = [(wr.test_metrics or {}).get("total_return_pct") for wr in evaluated]
    with w.bordered_card():
        w.section_title("Retorno por ventana", "Entrenamiento (suave) frente a test (intenso). Un test muy peor que su entrenamiento sugiere sobreajuste.")
        ui.plotly(charts.walkforward_bars(labels, train_r, test_r, hover)).classes("w-full")

    if result.stitched_result is not None:
        with w.bordered_card():
            m = result.stitched_metrics
            w.section_title(
                "Curva fuera de muestra unida",
                f"Las ventanas de test consecutivas encadenadas · Sharpe {format_metric('sharpe_ratio', m['sharpe_ratio'])} · "
                f"drawdown máx. {format_metric('max_drawdown_pct', m['max_drawdown_pct'])} · {m['num_trades']} operaciones.",
            )
            ui.plotly(charts.equity_drawdown_chart(result.stitched_result)).classes("w-full")

    _render_windows_table(result)


def _render_windows_table(result: WalkForwardResult) -> None:
    keys = sorted({k for wr in result.windows for k in wr.params}) if result.optimized else []
    changing = [k for k in keys if len({repr(wr.params.get(k)) for wr in result.windows}) > 1]
    rows = []
    for wr in result.windows:
        tm, rm = wr.test_metrics or {}, wr.train_metrics or {}
        row = {
            "n": wr.window.index + 1,
            "train_start": wr.window.train_start.strftime("%Y-%m-%d"),
            "train_end": wr.window.train_end.strftime("%Y-%m-%d"),
            "test_start": wr.window.test_start.strftime("%Y-%m-%d"),
            "test_end": wr.window.test_end.strftime("%Y-%m-%d"),
            "train_return": _r(rm.get("total_return_pct")), "train_sharpe": _r(rm.get("sharpe_ratio")),
            "return": _r(tm.get("total_return_pct")), "sharpe": _r(tm.get("sharpe_ratio")),
            "mdd": _r(tm.get("max_drawdown_pct")), "pf": _r(tm.get("profit_factor")),
            "trades": tm.get("num_trades"),
            "note": wr.skipped_reason or "",
        }
        row.update({f"p_{k}": wr.params.get(k) for k in changing})
        rows.append(row)
    columns = [
        {"name": "n", "label": "Ventana", "field": "n", "align": "left"},
        {"name": "train_start", "label": "Train inicio", "field": "train_start", "align": "left"},
        {"name": "train_end", "label": "Train fin", "field": "train_end", "align": "left"},
        {"name": "test_start", "label": "Test inicio", "field": "test_start", "align": "left"},
        {"name": "test_end", "label": "Test fin", "field": "test_end", "align": "left"},
    ]
    columns += [{"name": f"p_{k}", "label": k, "field": f"p_{k}", "align": "right"} for k in changing]
    columns += [
        {"name": "train_return", "label": "Retorno train (%)", "field": "train_return", "align": "right", "sortable": True},
        {"name": "train_sharpe", "label": "Sharpe train", "field": "train_sharpe", "align": "right", "sortable": True},
        {"name": "return", "label": "Retorno test (%)", "field": "return", "align": "right", "sortable": True},
        {"name": "sharpe", "label": "Sharpe test", "field": "sharpe", "align": "right", "sortable": True},
        {"name": "mdd", "label": "MDD test (%)", "field": "mdd", "align": "right", "sortable": True},
        {"name": "pf", "label": "Profit factor", "field": "pf", "align": "right", "sortable": True},
        {"name": "trades", "label": "Operaciones", "field": "trades", "align": "right", "sortable": True},
        {"name": "note", "label": "Nota", "field": "note", "align": "left"},
    ]
    with w.bordered_card():
        w.section_title("Detalle por ventana", "Cada test arranca con el capital inicial y usa la historia previa solo como contexto.")
        with ui.element("div").classes("w-full overflow-x-auto"):
            table = ui.table(columns=columns, rows=rows, row_key="n", pagination=20).props("flat dense").classes("w-full")
        table.add_slot("body-cell-return", """
            <q-td :props="props" class="font-medium" :class="props.value > 0 ? '%s' : (props.value < 0 ? '%s' : '')">
                {{ props.value == null ? '—' : props.value }}
            </q-td>
        """ % (sign_class(1), NEG_CLASS))


def _r(value) -> float | None:
    return None if value is None else round(float(value), 3)
