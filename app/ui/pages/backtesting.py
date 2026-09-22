import datetime as dt

import plotly.graph_objects as go
from nicegui import ui
from sqlalchemy import select

from app.backtest.data import get_candles
from app.backtest.engine import Backtester, BacktestResult
from app.backtest.metrics import compute_metrics
from app.backtest.monte_carlo import run_monte_carlo
from app.backtest.optimizer import MAX_COMBINATIONS, build_param_grid, run_grid_search
from app.db.base import async_session
from app.db.models import BacktestRun
from app.strategies import registry
from app.ui.layout import render_nav
from app.ui.param_form import render_param_form

METRIC_LABELS = {
    "final_equity": "Equity final",
    "total_return_pct": "Retorno total (%)",
    "cagr_pct": "CAGR (%)",
    "max_drawdown_pct": "Drawdown maximo (%)",
    "max_drawdown_duration_days": "Duracion max. drawdown (dias)",
    "sharpe_ratio": "Sharpe",
    "sortino_ratio": "Sortino",
    "calmar_ratio": "Calmar",
    "num_trades": "Cantidad de operaciones",
    "win_rate_pct": "% operaciones ganadoras",
    "profit_factor": "Profit factor",
    "avg_win": "Ganancia promedio",
    "avg_loss": "Perdida promedio",
    "expectancy": "Expectativa por operacion",
    "exposure_pct": "% tiempo en mercado",
}

TIMEFRAME_OPTIONS = {"1": "1 min", "5": "5 min", "15": "15 min", "60": "1 hora", "240": "4 horas", "D": "1 dia"}


def _equity_chart(result: BacktestResult) -> go.Figure:
    equity = result.equity_curve
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    # pandas Timestamp es subclase de datetime, y el serializador JSON de
    # NiceGUI (orjson) no acepta subclases de datetime: hay que pasarlas a
    # datetime nativo antes de meterlas en la figura de Plotly.
    x_equity = equity.index.to_pydatetime()
    x_drawdown = drawdown.index.to_pydatetime()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x_equity, y=equity.values, name="Equity", line=dict(color="#2E86DE")))
    fig.add_trace(
        go.Scatter(
            x=x_drawdown, y=drawdown.values, name="Drawdown %", yaxis="y2",
            line=dict(color="#E74C3C", width=1), fill="tozeroy", fillcolor="rgba(231,76,60,0.15)",
        )
    )
    for t in result.trades:
        if t.exit_time is None:
            continue
        # los marcadores van sobre la curva de EQUITY (no el precio de la
        # operacion, que esta en una escala totalmente distinta)
        color = "#27AE60" if (t.pnl or 0) > 0 else "#E74C3C"
        fig.add_trace(
            go.Scatter(
                x=[t.entry_time.to_pydatetime(), t.exit_time.to_pydatetime()],
                y=[equity.asof(t.entry_time), equity.asof(t.exit_time)],
                mode="lines+markers", line=dict(color=color, width=1, dash="dot"),
                marker=dict(color=color, size=6, symbol="circle"),
                showlegend=False, hovertext=f"{t.side} pnl={t.pnl:.2f}" if t.pnl is not None else t.side,
            )
        )
    fig.update_layout(
        yaxis=dict(title="Equity"),
        yaxis2=dict(title="Drawdown %", overlaying="y", side="right", showgrid=False),
        margin=dict(l=40, r=40, t=20, b=30),
        legend=dict(orientation="h"),
        height=420,
    )
    return fig


def _histogram(values: list[float], title: str, color: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=values, nbinsx=40, marker_color=color))
    fig.update_layout(title=title, height=300, margin=dict(l=30, r=20, t=40, b=30))
    return fig


def _bar_chart(labels: list[str], values: list[float], title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=labels, y=values, marker_color="#2E86DE"))
    fig.update_layout(title=title, height=350, margin=dict(l=30, r=20, t=40, b=80), xaxis=dict(tickangle=-45))
    return fig


async def _save_run(strategy_key, symbol, timeframe, params, start, end, metrics) -> None:
    async with async_session() as session:
        session.add(
            BacktestRun(
                strategy_key=strategy_key, symbol=symbol, timeframe=timeframe,
                params=params, start_date=start, end_date=end, metrics=metrics,
            )
        )
        await session.commit()


async def _load_recent_runs(limit: int = 15) -> list[BacktestRun]:
    async with async_session() as session:
        result = await session.execute(
            select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())


@ui.page("/backtesting")
async def backtesting_page() -> None:
    render_nav("/backtesting")
    state: dict = {"result": None, "timeframe": None}

    with ui.column().classes("w-full p-4 gap-4"):
        ui.label("Backtesting").classes("text-xl font-bold")

        with ui.row().classes("gap-4 items-end flex-wrap"):
            strategy_select = ui.select(
                {key: cls.display_name for key, cls in registry.get_all().items()},
                label="Estrategia", value=next(iter(registry.get_all()), None),
            ).classes("w-56")
            symbol_input = ui.input("Simbolo", value="BTCUSDT").classes("w-32")
            timeframe_select = ui.select(TIMEFRAME_OPTIONS, label="Timeframe", value="60").classes("w-32")
            days_input = ui.number("Dias de historia", value=90, min=7, max=730).classes("w-32")
            capital_input = ui.number("Capital inicial", value=1000.0, min=10).classes("w-32")
            fee_input = ui.number("Fee por lado (%)", value=0.055, step=0.005, format="%.3f").classes("w-32")
            leverage_input = ui.number("Apalancamiento max.", value=10, min=1, max=100).classes("w-32")

        with ui.tabs().classes("w-full") as tabs:
            tab_simple = ui.tab("Backtest simple")
            tab_opt = ui.tab("Optimizacion de parametros")

        with ui.tab_panels(tabs, value=tab_simple).classes("w-full"):
            # ---------------- Backtest simple ----------------
            with ui.tab_panel(tab_simple):
                ui.label("Parametros de la estrategia").classes("font-bold")
                param_form_container = ui.row().classes("gap-4 flex-wrap")
                get_params_holder: dict = {}

                def rebuild_param_form() -> None:
                    param_form_container.clear()
                    strategy_cls = registry.get(strategy_select.value)
                    with param_form_container:
                        get_params_holder["get"] = render_param_form(strategy_cls.params_model)

                rebuild_param_form()
                strategy_select.on_value_change(lambda _: rebuild_param_form())

                run_button = ui.button("Ejecutar backtest")
                results_container = ui.column().classes("w-full gap-4")

                async def run_backtest() -> None:
                    results_container.clear()
                    run_button.props("loading")
                    try:
                        strategy_cls = registry.get(strategy_select.value)
                        params = get_params_holder["get"]()
                        strategy = strategy_cls(params)

                        end = dt.datetime.now(dt.timezone.utc)
                        start = end - dt.timedelta(days=days_input.value)
                        candles = await get_candles(symbol_input.value, timeframe_select.value, start, end)

                        with results_container:
                            if candles.empty:
                                ui.label("No se encontraron velas para ese simbolo/rango.").classes("text-red-600")
                                return

                        result = Backtester(
                            fee_pct=fee_input.value, max_leverage=leverage_input.value
                        ).run(strategy, candles, capital_input.value)
                        metrics = compute_metrics(result, timeframe_select.value)
                        state["result"] = result
                        state["timeframe"] = timeframe_select.value

                        with results_container:
                            if "error" in metrics:
                                ui.label(metrics["error"]).classes("text-red-600")
                                return

                            with ui.row().classes("gap-6 flex-wrap"):
                                for key, label in METRIC_LABELS.items():
                                    if key in metrics:
                                        with ui.card().classes("p-2"):
                                            ui.label(label).classes("text-xs text-gray-500")
                                            ui.label(str(metrics[key])).classes("text-lg font-bold")

                            ui.plotly(_equity_chart(result)).classes("w-full")

                            with ui.expansion(f"Operaciones ({len(result.trades)})").classes("w-full"):
                                rows = [
                                    {
                                        "entrada": t.entry_time.strftime("%Y-%m-%d %H:%M"),
                                        "lado": t.side,
                                        "precio_entrada": round(t.entry_price, 2),
                                        "salida": t.exit_time.strftime("%Y-%m-%d %H:%M") if t.exit_time else "-",
                                        "precio_salida": round(t.exit_price, 2) if t.exit_price else None,
                                        "pnl": round(t.pnl, 2) if t.pnl is not None else None,
                                        "motivo": t.reason,
                                        "salida_por": t.exit_reason,
                                    }
                                    for t in result.trades
                                ]
                                ui.table(
                                    columns=[{"name": c, "label": c, "field": c} for c in
                                              ["entrada", "lado", "precio_entrada", "salida", "precio_salida", "pnl", "salida_por", "motivo"]],
                                    rows=rows,
                                ).classes("w-full")

                            ui.separator()
                            ui.label("Robustez (Monte Carlo)").classes("font-bold")
                            ui.label(
                                "Reordena (o remuestrea) las operaciones de esta corrida miles de veces para "
                                "ver si el resultado depende de la suerte en el orden en que cayeron, o es "
                                "consistente. 'Shuffle' conserva el retorno final y varia solo el camino "
                                "(drawdown); 'Bootstrap' tambien varia la composicion de operaciones."
                            ).classes("text-sm text-gray-500")

                            with ui.row().classes("gap-4 items-end"):
                                mc_method = ui.select({"shuffle": "Shuffle (reordenar)", "bootstrap": "Bootstrap (remuestreo)"}, value="shuffle").classes("w-56")
                                mc_sims = ui.number("Simulaciones", value=1000, min=100, max=20000).classes("w-32")
                                mc_button = ui.button("Correr Monte Carlo")

                            mc_container = ui.column().classes("w-full gap-4")

                            async def run_mc() -> None:
                                mc_container.clear()
                                current: BacktestResult = state["result"]
                                mc = run_monte_carlo(
                                    current.trades, capital_input.value,
                                    n_sims=int(mc_sims.value), method=mc_method.value, seed=None,
                                )
                                with mc_container:
                                    if mc is None:
                                        ui.label(
                                            "Hacen falta al menos 10 operaciones cerradas para correr Monte Carlo."
                                        ).classes("text-orange-600")
                                        return
                                    with ui.row().classes("gap-6 flex-wrap"):
                                        for label, val in [
                                            ("Retorno p5 (%)", mc.return_pct_p5),
                                            ("Retorno mediana (%)", mc.return_pct_p50),
                                            ("Retorno p95 (%)", mc.return_pct_p95),
                                            ("Drawdown peor caso (%)", mc.max_dd_p5),
                                            ("Drawdown mediana (%)", mc.max_dd_p50),
                                            ("Prob. de perdida (%)", mc.prob_loss_pct),
                                            (f"Prob. DD > {mc.ruin_threshold_pct:.0f}% (%)", mc.prob_ruin_pct),
                                        ]:
                                            with ui.card().classes("p-2"):
                                                ui.label(label).classes("text-xs text-gray-500")
                                                ui.label(str(val)).classes("text-lg font-bold")
                                    with ui.row().classes("gap-4 flex-wrap"):
                                        ui.plotly(_histogram(mc.returns_distribution, "Distribucion de retorno final (%)", "#2E86DE"))
                                        ui.plotly(_histogram(mc.drawdowns_distribution, "Distribucion de drawdown maximo (%)", "#E74C3C"))

                            mc_button.on_click(run_mc)

                        await _save_run(
                            strategy_cls.key, symbol_input.value, timeframe_select.value,
                            params.model_dump(), start, end, metrics,
                        )
                        await refresh_recent_runs()
                    finally:
                        run_button.props(remove="loading")

                run_button.on_click(run_backtest)

                ui.separator()
                ui.label("Corridas guardadas").classes("font-bold")
                recent_runs_container = ui.column().classes("w-full gap-2")

                async def refresh_recent_runs() -> None:
                    recent_runs_container.clear()
                    runs = await _load_recent_runs()
                    with recent_runs_container:
                        if not runs:
                            ui.label("Todavia no corriste ningun backtest.").classes("text-gray-500")
                        for run in runs:
                            m = run.metrics
                            with ui.card().classes("w-full p-2"):
                                ui.label(
                                    f"{run.created_at.strftime('%Y-%m-%d %H:%M')} · {run.strategy_key} · "
                                    f"{run.symbol} {run.timeframe} · params={run.params}"
                                ).classes("text-xs text-gray-500")
                                ui.label(
                                    f"Retorno: {m.get('total_return_pct')}% · Sharpe: {m.get('sharpe_ratio')} · "
                                    f"Max DD: {m.get('max_drawdown_pct')}% · Trades: {m.get('num_trades')}"
                                ).classes("text-sm")

                await refresh_recent_runs()

            # ---------------- Optimizacion ----------------
            with ui.tab_panel(tab_opt):
                ui.label(
                    "Definí un rango (min, max, paso) para cada parámetro que quieras barrer. "
                    f"Dejalo en blanco para usar el valor por defecto. Máximo {MAX_COMBINATIONS} combinaciones."
                ).classes("text-sm text-gray-500")

                opt_form_container = ui.column().classes("w-full gap-2")
                opt_fields_holder: dict = {}

                def rebuild_opt_form() -> None:
                    opt_form_container.clear()
                    strategy_cls = registry.get(strategy_select.value)
                    opt_fields_holder.clear()
                    with opt_form_container:
                        for name, f in strategy_cls.params_model.model_fields.items():
                            is_numeric = f.annotation in (int, float)
                            default = f.default
                            with ui.row().classes("gap-2 items-center"):
                                ui.label(f.description or name).classes("w-56 text-sm")
                                if not is_numeric:
                                    ui.label(f"(no numerico, se usa default={default})").classes("text-xs text-gray-500")
                                    continue
                                min_in = ui.number("min", value=default).classes("w-24")
                                max_in = ui.number("max", value=default).classes("w-24")
                                step_in = ui.number("paso", value=1 if f.annotation is int else 0.1).classes("w-24")
                                opt_fields_holder[name] = (min_in, max_in, step_in, f.annotation, default)

                rebuild_opt_form()
                strategy_select.on_value_change(lambda _: rebuild_opt_form())

                rank_select = ui.select(
                    {k: v for k, v in METRIC_LABELS.items() if k not in ("num_trades",)},
                    label="Ordenar por", value="sharpe_ratio",
                ).classes("w-64")
                opt_button = ui.button("Ejecutar barrido")
                opt_results = ui.column().classes("w-full gap-4")

                async def run_optimization() -> None:
                    opt_results.clear()
                    opt_button.props("loading")
                    try:
                        strategy_cls = registry.get(strategy_select.value)
                        param_ranges = {}
                        for name, (min_in, max_in, step_in, annotation, default) in opt_fields_holder.items():
                            lo, hi, step = min_in.value, max_in.value, step_in.value
                            if lo == hi or not step:
                                param_ranges[name] = [default]
                                continue
                            values = []
                            v = lo
                            while v <= hi + 1e-9:
                                values.append(int(round(v)) if annotation is int else round(v, 6))
                                v += step
                            param_ranges[name] = values

                        grid = build_param_grid(param_ranges)
                        with opt_results:
                            if len(grid) > MAX_COMBINATIONS:
                                ui.label(
                                    f"El barrido tiene {len(grid)} combinaciones (maximo {MAX_COMBINATIONS}). "
                                    "Reduci el rango o aumenta el paso."
                                ).classes("text-red-600")
                                return

                        end = dt.datetime.now(dt.timezone.utc)
                        start = end - dt.timedelta(days=days_input.value)
                        candles = await get_candles(symbol_input.value, timeframe_select.value, start, end)

                        results = run_grid_search(
                            strategy_cls, grid, candles, capital_input.value,
                            timeframe_select.value, fee_input.value, rank_by=rank_select.value,
                            max_leverage=leverage_input.value,
                        )

                        with opt_results:
                            if not results:
                                ui.label("Ninguna combinacion produjo resultados validos.").classes("text-orange-600")
                                return

                            labels = [str(r["params"]) for r in results[:30]]
                            values = [r.get(rank_select.value) or 0 for r in results[:30]]
                            ui.plotly(_bar_chart(labels, values, f"{METRIC_LABELS[rank_select.value]} por combinacion")).classes("w-full")

                            columns = [{"name": "params", "label": "Parametros", "field": "params"}] + [
                                {"name": k, "label": v, "field": k} for k, v in METRIC_LABELS.items()
                            ]
                            rows = [{"params": str(r["params"]), **{k: r.get(k) for k in METRIC_LABELS}} for r in results]
                            ui.table(columns=columns, rows=rows).classes("w-full")
                    finally:
                        opt_button.props(remove="loading")

                opt_button.on_click(run_optimization)
