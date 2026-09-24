"""Pestaña Robustness: sensibilidad a parametros (1 o 2) y barrido multi-parametro."""

import asyncio
import logging

from nicegui import ui
from pydantic import ValidationError

from app.backtest.config import ConfigError
from app.backtest.engine import DataError
from app.backtest.optimizer import MAX_COMBINATIONS, build_param_grid, expand_range, run_grid_search
from app.backtest.robustness import (
    NEUTRAL_VALUE,
    SENSITIVITY_METRICS,
    SensitivityResult,
    heatmap_matrix,
    neighbor_comparison,
    numeric_params,
    param_bounds,
    run_sensitivity,
    stability_summary,
)
from app.backtest.service import prepare_market_data
from app.ui import backtest_charts as charts
from app.ui import backtest_widgets as w
from app.ui.backtest_axes import NONE, AxesSelector, default_range, within_bounds  # noqa: F401
from app.ui.backtest_config_panel import ConfigPanel
from app.ui.backtest_format import METRIC_LABELS, NEG_CLASS, fmt_pct, fmt_usd, format_metric, sign_class

logger = logging.getLogger(__name__)
_SIGNED_COLUMNS = ("total_return_pct", "cagr_pct", "expectancy")


def _friendly(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "Parámetros de la estrategia inválidos: " + "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
        )
    if isinstance(exc, (ConfigError, DataError, ValueError)):
        return str(exc)
    return f"No se pudo completar el análisis: {exc}"


def _outlined(element):
    return element.props("outlined dense").classes("w-full")


# --------------------------------------------------------------- sensitivity

def build_robustness_tab(panel: ConfigPanel) -> None:
    with ui.column().classes("w-full gap-5"):
        _build_sensitivity(panel)
        with ui.expansion("Barrido multi-parámetro (avanzado)", icon="grid_on").classes(
            "w-full border border-gray-200 rounded-xl bg-white"
        ):
            with ui.column().classes("w-full gap-4 p-3"):
                _build_multi_sweep(panel)


def _build_sensitivity(panel: ConfigPanel) -> None:
    holder: dict = {}

    def update_count() -> None:
        text, too_many = selector.count_label()
        holder["label"].text = text
        holder["label"].classes(replace=AxesSelector.label_classes(too_many))
        holder["button"].set_enabled(not too_many)

    with w.bordered_card():
        w.section_title(
            "Sensibilidad a parámetros",
            "Movés uno o dos parámetros y mirás si los resultados son estables. No se elige ningún ganador: "
            "buscá zonas amplias donde la estrategia se comporta parecido, no un pico aislado.",
        )
        selector = AxesSelector(panel, allow_second=True, on_change=lambda: update_count() if "button" in holder else None)
        with ui.row().classes("w-full items-end justify-between gap-4"):
            holder["label"] = ui.label("").classes("text-sm font-medium text-gray-700")
            holder["button"] = ui.button("Calcular sensibilidad", icon="play_arrow").props("unelevated no-caps")
    update_count()

    results_box = ui.column().classes("w-full gap-4")
    with results_box:
        w.empty_state("tune", "Elegí uno o dos parámetros y calculá la sensibilidad.")

    async def run() -> None:
        holder["button"].props("loading")
        results_box.clear()
        with results_box:
            w.loading_state("Descargando velas y simulando cada combinación…")
        try:
            strategy_cls, config = panel.strategy_cls, panel.collect()
            axes = selector.axes()
            base = panel.get_params().model_dump()
            market = await prepare_market_data(config, panel.strategy_cls)
            if market.candles.empty:
                raise ValueError("No se encontraron velas para ese símbolo y rango.")
            result = await asyncio.to_thread(run_sensitivity, strategy_cls, base, axes, config, market)
            results_box.clear()
            with results_box:
                render_sensitivity(result, market.quality.get("warnings", []))
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, (ConfigError, DataError, ValidationError, ValueError)):
                logger.exception("Fallo la sensibilidad")
            results_box.clear()
            with results_box:
                w.notice(_friendly(exc), "negative")
        finally:
            holder["button"].props(remove="loading")

    holder["button"].on_click(run)


def render_sensitivity(result: SensitivityResult, data_warnings: list[str]) -> None:
    for text in data_warnings:
        w.notice(text, "warning")
    summary = stability_summary(result)
    if not result.points:
        w.notice("Ninguna combinación produjo resultados válidos.", "warning")
        return

    with ui.element("div").classes("grid grid-cols-2 md:grid-cols-5 gap-4 w-full"):
        w._tile("Combinaciones con retorno > 0", fmt_pct(summary["pct_positive_return"], 0),
                f"de {summary['n_combinations']} probadas", "Qué porción de la grilla termina en ganancia. Un valor alto y parejo indica una zona estable.")
        w._tile("Retorno (mín · mediana · máx)", f"{fmt_pct(summary['return_median'], 1, signed=True)}",
                f"{fmt_pct(summary['return_min'], 1, signed=True)} a {fmt_pct(summary['return_max'], 1, signed=True)}",
                "Rango de retornos en toda la grilla.", color_class=sign_class(summary["return_median"]))
        w._tile("Sharpe mediano", format_metric("sharpe_ratio", summary["sharpe_median"]), None,
                "Mediana del Sharpe de todas las combinaciones.")
        w._tile("Peor drawdown", fmt_pct(summary["worst_drawdown"]), None, "El peor drawdown máximo de la grilla.")
        few = summary["pct_few_trades"]
        w._tile("Operaciones por combinación", f"{summary['trades_min']} – {summary['trades_max']}",
                f"{fmt_pct(few, 0)} con menos de 30", "Con pocas operaciones las métricas son poco confiables.")
    if summary["pct_few_trades"] and summary["pct_few_trades"] > 50:
        w.notice("Más de la mitad de las combinaciones tiene menos de 30 operaciones: los resultados son poco confiables.", "warning")

    cmp = neighbor_comparison(result, "total_return_pct")
    if cmp:
        gap = cmp["difference"]
        text = (
            f"El punto actual rinde {fmt_pct(cmp['base'], 2, signed=True)}; sus vecinos inmediatos rinden entre "
            f"{fmt_pct(cmp['neighbor_min'], 2, signed=True)} y {fmt_pct(cmp['neighbor_max'], 2, signed=True)} "
            f"(mediana {fmt_pct(cmp['neighbor_median'], 2, signed=True)})."
        )
        if cmp["base"] > cmp["neighbor_max"] and gap > 0:
            text += " Es un máximo local: si el resultado depende de este valor exacto, puede ser ruido y no una zona robusta."
        w.notice(text, "warning" if cmp["base"] > cmp["neighbor_max"] else "info")

    if len(result.axes) == 1:
        with w.bordered_card():
            w.section_title(
                f"Efecto de {result.names[0]}",
                "Una línea por métrica, cada una en su propia escala. La línea punteada vertical marca el valor actual.",
            )
            labels = {m: METRIC_LABELS[m] for m in SENSITIVITY_METRICS}
            ui.plotly(charts.sensitivity_lines(result, labels, NEUTRAL_VALUE)).classes("w-full")
    else:
        _render_heatmap_card(result)

    _render_points_table(result)


def _render_heatmap_card(result: SensitivityResult) -> None:
    x_name, y_name = result.names
    with w.bordered_card():
        w.section_title(
            f"{x_name} × {y_name}",
            "Rojo = mal, azul = bien, gris = neutro (0, o 1 para el profit factor). El recuadro negro es el punto actual. "
            "Buscá bloques contiguos de colores parecidos.",
        )
        metric_select = ui.toggle(
            {m: METRIC_LABELS[m] for m in SENSITIVITY_METRICS}, value="total_return_pct"
        ).props("no-caps unelevated")
        plot_box = ui.column().classes("w-full")

        def draw(metric: str) -> None:
            plot_box.clear()
            xs, ys, z = heatmap_matrix(result, metric)
            base = (result.base_params.get(x_name), result.base_params.get(y_name))
            with plot_box:
                ui.plotly(charts.sensitivity_heatmap(
                    xs, ys, z, x_name, y_name, METRIC_LABELS[metric], NEUTRAL_VALUE.get(metric), base,
                )).classes("w-full")

        metric_select.on_value_change(lambda e: draw(e.value))
        draw("total_return_pct")


def _render_points_table(result: SensitivityResult) -> None:
    shown = [*SENSITIVITY_METRICS, "num_trades"]
    columns = [{"name": f"p_{n}", "label": n, "field": f"p_{n}", "align": "left"} for n in result.names]
    columns += [{"name": m, "label": METRIC_LABELS[m], "field": m, "align": "right", "sortable": True} for m in shown]
    rows = []
    for i, p in enumerate(result.points):
        row = {"id": i}
        row.update({f"p_{n}": p["params"][n] for n in result.names})
        row.update({m: (round(p[m], 4) if isinstance(p.get(m), float) else p.get(m)) for m in shown})
        rows.append(row)
    with w.bordered_card():
        w.section_title("Todas las combinaciones", "En el orden de la grilla, sin ranking. Tocá un encabezado para reordenar vos.")
        with ui.element("div").classes("w-full overflow-x-auto"):
            table = ui.table(columns=columns, rows=rows, row_key="id", pagination=15).props("flat dense").classes("w-full")
        for key in _SIGNED_COLUMNS:
            if key in shown:
                table.add_slot(f"body-cell-{key}", """
                    <q-td :props="props" class="font-medium"
                          :class="props.value > 0 ? '%s' : (props.value < 0 ? '%s' : '')">
                        {{ props.value == null ? '—' : props.value }}
                    </q-td>
                """ % (sign_class(1), NEG_CLASS))


# ------------------------------------------------------- multi-param sweep

def _build_multi_sweep(panel: ConfigPanel) -> None:
    ui.label(
        "Barre todos los parámetros a la vez (producto cartesiano). Útil para explorar; para juzgar robustez "
        "usá la sensibilidad de arriba y validá fuera de muestra."
    ).classes("text-sm text-gray-500")
    form_box = ui.element("div").classes("grid grid-cols-[minmax(0,2fr)_1fr_1fr_1fr] gap-x-3 gap-y-3 w-full items-center")
    fields: dict = {}
    holder: dict = {}

    def update_count() -> None:
        total = 1
        for min_in, max_in, step_in, is_int in fields.values():
            total *= max(len(expand_range(min_in.value, max_in.value, step_in.value, is_int)), 1)
        too_many = total > MAX_COMBINATIONS
        holder["label"].text = f"{total:,} combinaciones (máximo {MAX_COMBINATIONS})"
        holder["label"].classes(replace=f"text-sm font-medium {NEG_CLASS if too_many else 'text-gray-700'}")
        holder["button"].set_enabled(not too_many)

    def rebuild() -> None:
        form_box.clear()
        fields.clear()
        with form_box:
            for header in ("Parámetro", "Mínimo", "Máximo", "Paso"):
                ui.label(header).classes("text-xs uppercase tracking-wide text-gray-500")
            for name, field in panel.strategy_cls.params_model.model_fields.items():
                ui.label(field.description or name).classes("text-sm text-gray-800")
                if field.annotation not in (int, float):
                    ui.label(f"Fijo: {field.default}").classes("text-sm text-gray-500 col-span-3")
                    continue
                is_int = field.annotation is int
                min_in = _outlined(ui.number(value=field.default))
                max_in = _outlined(ui.number(value=field.default))
                step_in = _outlined(ui.number(value=1 if is_int else 0.1))
                for element in (min_in, max_in, step_in):
                    element.on_value_change(lambda _: update_count())
                fields[name] = (min_in, max_in, step_in, is_int)
        if "label" in holder:
            update_count()

    with ui.row().classes("w-full items-end justify-between gap-4"):
        holder["label"] = ui.label("").classes("text-sm font-medium text-gray-700")
        with ui.row().classes("items-end gap-3"):
            sort_select = ui.select(
                {"": "Orden de la grilla", **{k: v for k, v in METRIC_LABELS.items() if k != "num_trades"}},
                label="Ordenar por", value="",
            ).props("outlined dense").classes("w-56")
            holder["button"] = ui.button("Ejecutar barrido", icon="play_arrow").props("unelevated no-caps")
    rebuild()
    update_count()
    panel.on_strategy_change(rebuild)
    results_box = ui.column().classes("w-full gap-4")

    async def run() -> None:
        holder["button"].props("loading")
        results_box.clear()
        with results_box:
            w.loading_state("Descargando velas y probando combinaciones…")
        try:
            strategy_cls, config = panel.strategy_cls, panel.collect()
            ranges = {n: expand_range(a.value, b.value, c.value, is_int) for n, (a, b, c, is_int) in fields.items()}
            for name, field in strategy_cls.params_model.model_fields.items():
                ranges.setdefault(name, [field.default])
            grid = build_param_grid(ranges)
            market = await prepare_market_data(config, panel.strategy_cls)
            if market.candles.empty:
                raise ValueError("No se encontraron velas para ese símbolo y rango.")
            rank = sort_select.value or None
            results = await asyncio.to_thread(run_grid_search, strategy_cls, grid, config, market, rank)
            results_box.clear()
            with results_box:
                _render_multi_results(results)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, (ConfigError, DataError, ValidationError, ValueError)):
                logger.exception("Fallo el barrido de parametros")
            results_box.clear()
            with results_box:
                w.notice(_friendly(exc), "negative")
        finally:
            holder["button"].props(remove="loading")

    holder["button"].on_click(run)


def _render_multi_results(results: list[dict]) -> None:
    if not results:
        w.notice("Ninguna combinación produjo resultados válidos.", "warning")
        return
    varied = [k for k in results[0]["params"] if len({repr(r["params"][k]) for r in results}) > 1]
    label_keys = varied or list(results[0]["params"])
    columns = [{"name": "n", "label": "#", "field": "n", "align": "left"}]
    columns += [{"name": f"p_{k}", "label": k, "field": f"p_{k}", "align": "left"} for k in label_keys]
    shown = [k for k in METRIC_LABELS if k != "initial_equity"]
    columns += [{"name": k, "label": METRIC_LABELS[k], "field": k, "align": "right", "sortable": True} for k in shown]
    rows = []
    for i, r in enumerate(results):
        row = {"n": i + 1}
        row.update({f"p_{k}": r["params"][k] for k in label_keys})
        row.update({k: (round(r[k], 4) if isinstance(r.get(k), float) else r.get(k)) for k in shown})
        rows.append(row)
    with w.bordered_card():
        w.section_title(
            f"{len(results)} combinaciones",
            "Una combinación que rinde bien en el pasado no garantiza que rinda bien en el futuro: mirá si hay una zona estable.",
        )
        with ui.element("div").classes("w-full overflow-x-auto"):
            table = ui.table(columns=columns, rows=rows, row_key="n", pagination=15).props("flat dense").classes("w-full")
        for key in _SIGNED_COLUMNS:
            table.add_slot(f"body-cell-{key}", """
                <q-td :props="props" class="font-medium"
                      :class="props.value > 0 ? '%s' : (props.value < 0 ? '%s' : '')">
                    {{ props.value == null ? '—' : props.value }}
                </q-td>
            """ % (sign_class(1), NEG_CLASS))
