import asyncio
import logging

from nicegui import ui
from pydantic import ValidationError

from app.backtest.config import ConfigError
from app.backtest.engine import DataError
from app.backtest.history import run_fields
from app.backtest.monte_carlo import run_monte_carlo
from app.backtest.service import BacktestOutput, run_backtest as run_backtest_service
from app.db.base import async_session
from app.db.models import BacktestRun
from app.ui import backtest_widgets as w
from app.ui.backtest_config_panel import ConfigPanel
from app.ui.backtest_robustness import build_robustness_tab
from app.ui.backtest_validation import build_validation_tab
from app.exports import backtest_tables
from app.strategies import registry
from app.ui.backtest_format import EXIT_REASON_LABELS, METRIC_GROUPS, METRIC_LABELS
from app.ui.backtest_history import build_history_tab
from app.ui.backtest_stress import build_stress_tab
from app.ui.export_button import export_button
from app.ui.layout import page_content, render_nav

logger = logging.getLogger(__name__)

_MC_LABELS = {"shuffle": "Shuffle de operaciones", "bootstrap": "Bootstrap", "block_bootstrap": "Block bootstrap"}


async def _save_run(output: BacktestOutput, params: dict) -> None:
    """Guarda la corrida completa: configuracion, version de la estrategia, operaciones y curva de capital."""
    async with async_session() as session:
        session.add(BacktestRun(**run_fields(output, params)))
        await session.commit()


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return f"Parámetros de la estrategia inválidos: {details}"
    if isinstance(exc, (ConfigError, DataError, ValueError)):
        return str(exc)
    return f"No se pudo completar el backtest: {exc}"


@ui.page("/backtesting")
async def backtesting_page() -> None:
    render_nav("/backtesting")
    state: dict = {"output": None, "params": None}

    with page_content(
        "Backtesting", "Investigá una estrategia sobre datos históricos reales de Bybit: performance, riesgo, operaciones y robustez.",
        width="max-w-[1400px]",
    ):

        panel = ConfigPanel()
        with ui.row().classes("w-full justify-end"):
            run_button = ui.button("Ejecutar backtest", icon="play_arrow").props("unelevated no-caps")

        header_box = ui.column().classes("w-full")

        with ui.tabs().props("align=left no-caps inline-label dense").classes("w-full") as tabs:
            t_overview = ui.tab("Overview", icon="dashboard")
            t_equity = ui.tab("Equity", icon="show_chart")
            t_trades = ui.tab("Trades", icon="list_alt")
            t_risk = ui.tab("Risk", icon="shield")
            t_mc = ui.tab("Monte Carlo", icon="casino")
            t_rob = ui.tab("Robustness", icon="tune")
            t_val = ui.tab("Validation", icon="fact_check")
            t_stress = ui.tab("Stress Test", icon="warning_amber")
            t_history = ui.tab("History", icon="history")

        boxes: dict[str, ui.column] = {}
        with ui.tab_panels(tabs, value=t_overview, animated=False).classes("w-full bg-transparent"):
            for name, tab in (("overview", t_overview), ("equity", t_equity), ("trades", t_trades), ("risk", t_risk), ("mc", t_mc)):
                with ui.tab_panel(tab).classes("p-0 pt-4"):
                    boxes[name] = ui.column().classes("w-full gap-4")
                    with boxes[name]:
                        w.empty_state("query_stats", "Ejecutá un backtest para ver los resultados acá.")

            # ---------------- Robustness ----------------
            with ui.tab_panel(t_rob).classes("p-0 pt-4"):
                build_robustness_tab(panel)

            # ---------------- Validation ----------------
            with ui.tab_panel(t_val).classes("p-0 pt-4"):
                build_validation_tab(panel)

            # ---------------- Stress Test ----------------
            with ui.tab_panel(t_stress).classes("p-0 pt-4"):
                build_stress_tab(panel)

            # ---------------- History ----------------
            with ui.tab_panel(t_history).classes("p-0 pt-4"):
                with ui.column().classes("w-full gap-4"):
                    refresh_history = build_history_tab(
                        on_view=lambda output: show_saved_run(output),
                        on_clone=lambda config, key, params: clone_run(config, key, params),
                    )

        def show_saved_run(output: BacktestOutput) -> None:
            """Ver: muestra una corrida guardada en las pestañas de resultados, como si se acabara de correr."""
            state["output"], state["params"] = output, output.result.params
            render_results(output)
            tabs.set_value(t_overview)
            ui.run_javascript("window.scrollTo({top: 0, behavior: 'smooth'})")

        def clone_run(config, strategy_key: str, params: dict) -> None:
            """Clonar: carga la configuracion de una corrida guardada en el panel de arriba."""
            panel.load(config, strategy_key, params)
            ui.run_javascript("window.scrollTo({top: 0, behavior: 'smooth'})")

        def build_mc_tab(output: BacktestOutput) -> None:
            w.mc_intro()
            summary = ui.label("").classes("text-sm text-gray-600")
            run_mc_button = ui.button("Correr simulación", icon="casino").props("unelevated no-caps")
            mc_box = ui.column().classes("w-full gap-4")

            async def run_mc() -> None:
                mc_box.clear()
                try:
                    cfg = panel.collect()
                    cfg.validate_or_raise()
                    v = cfg.validation
                except Exception as exc:  # noqa: BLE001
                    with mc_box:
                        w.notice(_friendly_error(exc), "negative")
                    return
                summary.text = (
                    f"Método: {_MC_LABELS[v.mc_method]} · {v.mc_sims:,} simulaciones · "
                    f"semilla {v.mc_seed if v.mc_seed is not None else 'aleatoria (queda registrada en el resultado)'}"
                )
                mc = await asyncio.to_thread(
                    run_monte_carlo, state["output"].result.trades, output.config.initial_capital,
                    n_sims=v.mc_sims, method=v.mc_method, ruin_threshold_pct=v.mc_ruin_threshold_pct, seed=v.mc_seed,
                    block_size=v.mc_block_size,
                )
                with mc_box:
                    if mc is None:
                        w.notice("Hacen falta al menos 10 operaciones cerradas para correr Monte Carlo.", "warning")
                        return
                    w.render_mc_results(mc)

            run_mc_button.on_click(run_mc)

        def render_results(output: BacktestOutput) -> None:
            header_box.clear()
            with header_box:
                strategy_name = registry.get(output.result.strategy_key).display_name
                w.results_header(strategy_name, output.config, output.result, output.strategy_version)
                with ui.row().classes("w-full justify-end"):
                    export_button(
                        lambda o=output: backtest_tables(
                            o.result, o.metrics, o.config.to_dict(), strategy_name, o.strategy_version,
                            METRIC_GROUPS, METRIC_LABELS, EXIT_REASON_LABELS,
                        ),
                        f"backtest_{output.result.strategy_key}_{output.config.symbol}_{output.config.timeframe}",
                        ["Operaciones", "Equity", "Métricas"], label="Descargar resultados",
                    )
            for box in boxes.values():
                box.clear()
            with boxes["overview"]:
                w.render_overview(output.metrics, output.result)
            with boxes["equity"]:
                w.render_equity_tab(output.result, output.market.candles)
            with boxes["trades"]:
                w.render_trades_tab(output.result)
            with boxes["risk"]:
                w.render_risk_tab(output.metrics)
            with boxes["mc"]:
                build_mc_tab(output)

        async def run_backtest() -> None:
            run_button.props("loading")
            for box in boxes.values():
                box.clear()
            with boxes["overview"]:
                w.loading_state("Descargando velas y simulando…")
            try:
                config = panel.collect()
                params = panel.get_params()
                output = await run_backtest_service(panel.strategy_cls, params, config)
                state["output"], state["params"] = output, params.model_dump()
                if "error" in output.metrics:
                    raise ValueError(output.metrics["error"])
                render_results(output)
                await _save_run(output, params.model_dump())
                await refresh_history()
            except Exception as exc:  # noqa: BLE001
                if not isinstance(exc, (ConfigError, DataError, ValidationError, ValueError)):
                    logger.exception("Fallo el backtest")
                for box in boxes.values():
                    box.clear()
                with boxes["overview"]:
                    w.notice(_friendly_error(exc), "negative")
            finally:
                run_button.props(remove="loading")

        run_button.on_click(run_backtest)
        await refresh_history()
