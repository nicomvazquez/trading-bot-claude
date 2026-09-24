"""Panel de configuracion del backtest, separado en:
A) parametros de la estrategia, B) riesgo, C) ejecucion, D) validacion."""

import datetime as dt
from typing import Callable

from nicegui import ui
from pydantic import BaseModel

from app.backtest.config import BacktestConfig, ConfigError, ExecutionConfig, RiskConfig, ValidationConfig
from app.strategies import registry
from app.strategies.base import Strategy
from app.timeutil import label as tz_label, local_midnight_utc, local_today, to_local
from app.ui import backtest_widgets as w
from app.ui.backtest_format import TIMEFRAME_OPTIONS
from app.ui.param_form import render_param_form


def _outlined(element):
    return element.props("outlined dense").classes("w-full")


def _number(label: str, value, **kwargs):
    return _outlined(ui.number(label, value=value, **kwargs))


def _section(title: str, icon: str, caption: str, opened: bool = False):
    return ui.expansion(title, icon=icon, caption=caption, value=opened).classes(
        "w-full border border-gray-200 rounded-xl bg-white"
    ).props("header-class=text-base")


def _grid(cols: str = "grid-cols-2 md:grid-cols-4"):
    return ui.element("div").classes(f"grid {cols} gap-4 w-full p-2 items-center")


def _optional(element) -> float | None:
    return None if element.value in (None, "") else float(element.value)


class ConfigPanel:
    def __init__(self) -> None:
        self._strategy_listeners: list[Callable[[], None]] = []
        self._range_listeners: list[Callable[[], None]] = []
        self._get_params: Callable[[], BaseModel] | None = None
        self._build()

    # ----------------------------------------------------------------- build

    def _build(self) -> None:
        strategies = registry.get_all()
        with w.bordered_card():
            w.section_title("Mercado y capital")
            with _grid().classes("p-0"):
                self.strategy = _outlined(ui.select(
                    {key: cls.display_name for key, cls in strategies.items()},
                    label="Estrategia", value=next(iter(strategies), None),
                )).classes("col-span-2")
                self.symbol = _outlined(ui.input("Símbolo", value="BTCUSDT"))
                self.timeframe = _outlined(ui.select(TIMEFRAME_OPTIONS, label="Timeframe", value="60"))
                self.capital = _number("Capital inicial (USD)", 1000.0, min=10).props("prefix=$")
                self._build_date_range()

        with ui.column().classes("w-full gap-3"):
            with _section("A · Parámetros de la estrategia", "psychology", "Lo que define cuándo entra y sale la estrategia", True):
                self._params_box = ui.element("div").classes(
                    "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 w-full p-2 items-center"
                )
            self._build_risk()
            self._build_execution()
            self._build_validation()

        self._rebuild_params()
        self.strategy.on_value_change(lambda _: self._on_strategy_changed())

    # ------------------------------------------------------------ rango de fechas

    _PRESETS = [("30 días", 30), ("90 días", 90), ("6 meses", 182), ("1 año", 365), ("2 años", 730)]

    def _build_date_range(self) -> None:
        """Selector de rango de fechas (hora local, Argentina): un calendario para elegir desde y hasta, mas atajos de uso comun."""
        today = local_today()
        with ui.column().classes("col-span-2 md:col-span-4 gap-2"):
            with ui.row().classes("w-full items-start gap-3 no-wrap"):
                self._range_input = ui.input(f"Rango de fechas · {tz_label()}").props("outlined dense readonly").classes("w-full max-w-md cursor-pointer")
                with self._range_input:
                    with ui.menu().props("no-parent-event") as menu:
                        # solo se pueden elegir dias hasta hoy: no hay velas del futuro
                        self._range_date = ui.date(on_change=lambda _: self._range_changed()).props(
                            f'range mask="YYYY-MM-DD" :options="d => d <= \'{today.strftime("%Y/%m/%d")}\'"'
                        )
                        with ui.row().classes("justify-end p-2"):
                            ui.button("Listo", on_click=menu.close).props("flat no-caps")
                with self._range_input.add_slot("append"):
                    ui.icon("event").classes("cursor-pointer")
                self._range_input.on("click", menu.open)
                self._range_hint = ui.label("").classes("text-sm text-gray-500 pt-2")
            with ui.row().classes("items-center gap-2 flex-wrap"):
                ui.label("Atajos:").classes("text-xs text-gray-500")
                for label, days in self._PRESETS:
                    ui.button(label, on_click=lambda d=days: self.set_last_days(d)).props("outline dense no-caps size=sm")
        self.set_last_days(90)

    def set_range(self, start: dt.date, end: dt.date) -> None:
        """Elige el rango de fechas (ambos dias incluidos)."""
        self._range_date.value = {"from": start.isoformat(), "to": end.isoformat()}

    def set_last_days(self, days: int) -> None:
        today = local_today()
        self.set_range(today - dt.timedelta(days=days - 1), today)

    def _range_dates(self) -> tuple[dt.date, dt.date] | None:
        value = self._range_date.value
        if isinstance(value, dict):  # rango completo
            first, last = value.get("from"), value.get("to")
        elif isinstance(value, str):  # se toco un solo dia: por ahora un rango de un dia
            first = last = value
        else:
            return None
        try:
            return dt.date.fromisoformat(first), dt.date.fromisoformat(last)
        except (TypeError, ValueError):
            return None

    def span_days(self) -> float:
        """Duracion del rango elegido, en dias (0 si no hay rango)."""
        dates = self._range_dates()
        return float((dates[1] - dates[0]).days + 1) if dates else 0.0

    def on_range_change(self, callback: Callable[[], None]) -> None:
        self._range_listeners.append(callback)

    def _range_changed(self) -> None:
        dates = self._range_dates()
        if dates is None:
            self._range_input.value, self._range_hint.text = "", "Elegí un rango de fechas."
        else:
            first, last = dates
            self._range_input.value = f"{first.strftime('%d/%m/%Y')} → {last.strftime('%d/%m/%Y')}"
            days = self.span_days()
            self._range_hint.text = f"{days:.0f} días" + ("" if days >= 365 else " · usá al menos 1 año para conclusiones firmes" if days >= 30 else " · rango corto: pocas operaciones")
        for callback in self._range_listeners:
            callback()

    def _period(self) -> tuple[dt.datetime, dt.datetime]:
        """Rango elegido como instantes UTC. Los dias son de la hora local: empieza a las 00:00 locales del primer dia
        (03:00 UTC en Argentina) y termina al final del ultimo (o ahora, si es hoy)."""
        dates = self._range_dates()
        if dates is None:
            raise ConfigError("Elegí un rango de fechas para el backtest.")
        first, last = dates
        start = local_midnight_utc(first)
        # fin = ultimo segundo del ultimo dia: la vela que abre a la medianoche siguiente ya no pertenece al rango
        end = local_midnight_utc(last + dt.timedelta(days=1)) - dt.timedelta(seconds=1)
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        return start, min(end, now)

    def _build_risk(self) -> None:
        with _section("B · Riesgo", "shield", "Cuánto se arriesga por operación y el tope de exposición"):
            with _grid():
                self.sizing_mode = _outlined(ui.select(
                    {"risk_based": "Por riesgo hasta el stop", "fixed_notional_pct": "Nocional fijo (% del equity)"},
                    label="Cálculo del tamaño", value="risk_based",
                )).classes("col-span-2")
                self.risk_pct = _number("Riesgo por operación (%)", None, min=0.01, max=100, step=0.1).props(
                    'hint="Vacío = usa el de la estrategia"'
                )
                self.notional_pct = _number("Nocional (% del equity)", 100.0, min=1, step=10)
                self.notional_pct.bind_enabled_from(self.sizing_mode, "value", backward=lambda v: v == "fixed_notional_pct")
                self.leverage = _number("Apalancamiento máx. (x)", 10, min=1, max=125, step=1)
                self.max_position_pct = _number("Posición máx. (% del equity)", None, min=1, step=10).props(
                    'hint="Vacío = solo tope por apalancamiento"'
                )
                self.risk_includes_costs = ui.switch("El riesgo incluye costos", value=False)
            ui.label(
                "El apalancamiento es un tope de exposición (nocional ≤ equity × apalancamiento): no define el tamaño de la "
                "operación, que sale del riesgo hasta el stop. El backtester opera una posición por vez (sin pyramiding); "
                "el límite de posiciones simultáneas aplica al bot en vivo."
            ).classes("text-xs text-gray-500 px-4 pb-3")

    def _build_execution(self) -> None:
        with _section("C · Ejecución", "bolt", "Tipo de orden, comisiones, slippage, spread y funding"):
            with _grid():
                self.execution_model = _outlined(ui.select(
                    {"next_open": "Apertura de la vela siguiente (recomendado)", "same_close": "Cierre de la misma vela (optimista)"},
                    label="Modelo de ejecución", value="next_open",
                )).classes("col-span-2")
                self.order_type = _outlined(ui.select(
                    {"market": "Market (taker)", "limit": "Limit al cierre de la señal (maker)"},
                    label="Tipo de orden de entrada", value="market",
                )).classes("col-span-2")
                self.limit_ttl = _number("Vigencia orden limit (velas)", 1, min=1, max=50, step=1)
                self.limit_ttl.bind_enabled_from(self.order_type, "value", backward=lambda v: v == "limit")
                self.taker_fee = _number("Fee taker (%)", 0.055, min=0, step=0.005, format="%.3f")
                self.maker_fee = _number("Fee maker (%)", 0.02, min=-0.05, step=0.005, format="%.3f")
                self.slippage = _number("Slippage (bps)", 0.0, min=0, step=0.5)
                self.spread = _number("Spread (bps)", 0.0, min=0, step=0.5)
                self.funding_mode = _outlined(ui.select(
                    {"none": "Sin funding", "constant": "Constante", "historical": "Histórico real (Bybit)"},
                    label="Funding", value="none",
                )).classes("col-span-2")
                self.funding_rate = _number("Funding (% por 8h)", 0.01, step=0.005, format="%.4f")
                self.funding_rate.bind_enabled_from(self.funding_mode, "value", backward=lambda v: v == "constant")
                self.intrabar = ui.switch("Resolver stop/TP ambiguos con timeframe menor", value=False)
            ui.label(
                "Stop-loss: orden stop-market (taker + slippage). Take-profit: orden limit (maker, sin slippage). "
                "Sin slippage, spread ni funding los resultados son optimistas: probá agregando valores para medir cuánto cambian."
            ).classes("text-xs text-gray-500 px-4 pb-3")

    def _build_validation(self) -> None:
        with _section("D · Validación", "verified", "Monte Carlo y semilla reproducible"):
            with _grid():
                self.mc_method = _outlined(ui.select(
                    {"shuffle": "Shuffle de operaciones", "bootstrap": "Bootstrap", "block_bootstrap": "Block bootstrap"},
                    label="Método Monte Carlo", value="shuffle",
                )).classes("col-span-2")
                self.mc_sims = _number("Simulaciones", 1000, min=10, max=50000, step=100)
                self.mc_seed = _number("Semilla", 42, step=1).props('hint="Vacío = aleatoria (no reproducible)"')
                self.mc_block = _number("Tamaño de bloque", 5, min=1, max=100, step=1)
                self.mc_block.bind_enabled_from(self.mc_method, "value", backward=lambda v: v == "block_bootstrap")
                self.mc_ruin = _number("Umbral de drawdown (%)", 20.0, min=1, max=100, step=1)

    # ------------------------------------------------------------ strategies

    @property
    def strategy_cls(self) -> type[Strategy]:
        return registry.get(self.strategy.value)

    def on_strategy_change(self, callback: Callable[[], None]) -> None:
        self._strategy_listeners.append(callback)

    def _on_strategy_changed(self) -> None:
        self._rebuild_params()
        for callback in self._strategy_listeners:
            callback()

    def _rebuild_params(self, initial: dict | None = None) -> None:
        self._params_box.clear()
        with self._params_box:
            self._get_params = render_param_form(self.strategy_cls.params_model, initial)

    def get_params(self) -> BaseModel:
        return self._get_params()

    # ---------------------------------------------------------------- config

    def load(self, config: BacktestConfig, strategy_key: str, params: dict | None) -> None:
        """Carga en el panel la configuracion de una corrida guardada (Clonar). Las opciones de estres
        (slippage extra en stops, funding adverso) no tienen control en el panel: no se cargan."""
        if strategy_key in registry.get_all() and self.strategy.value != strategy_key:
            self.strategy.value = strategy_key  # reconstruye el formulario y avisa a quienes dependen de la estrategia
        self._rebuild_params(params)
        for callback in self._strategy_listeners:
            callback()
        e, r, v = config.execution, config.risk, config.validation
        self.symbol.value, self.timeframe.value, self.capital.value = config.symbol, config.timeframe, config.initial_capital
        if config.start and config.end:
            self.set_range(to_local(config.start).date(), to_local(config.end - dt.timedelta(seconds=1)).date())
        else:
            self.set_last_days(config.days)
        self.sizing_mode.value, self.risk_pct.value, self.notional_pct.value = r.sizing_mode, r.risk_per_trade_pct, r.notional_pct_of_equity
        self.leverage.value, self.max_position_pct.value, self.risk_includes_costs.value = r.max_leverage, r.max_position_pct_of_equity, r.risk_includes_costs
        self.execution_model.value, self.order_type.value, self.limit_ttl.value = e.execution_model, e.order_type, e.limit_ttl_bars
        self.taker_fee.value, self.maker_fee.value, self.slippage.value, self.spread.value = e.taker_fee_pct, e.maker_fee_pct, e.slippage_bps, e.spread_bps
        self.funding_mode.value, self.funding_rate.value, self.intrabar.value = e.funding_mode, e.funding_rate_pct, e.intrabar_resolution
        self.mc_method.value, self.mc_sims.value, self.mc_seed.value = v.mc_method, v.mc_sims, v.mc_seed
        self.mc_block.value, self.mc_ruin.value = v.mc_block_size, v.mc_ruin_threshold_pct

    def collect(self) -> BacktestConfig:
        """Arma la configuracion desde los widgets (la valida quien la usa)."""
        seed = self.mc_seed.value
        start, end = self._period()
        return BacktestConfig(
            symbol=(self.symbol.value or "").strip().upper(),
            timeframe=self.timeframe.value,
            days=max(1, int((end - start).total_seconds() // 86400)),
            start=start, end=end,
            initial_capital=float(self.capital.value or 0),
            execution=ExecutionConfig(
                execution_model=self.execution_model.value,
                order_type=self.order_type.value,
                limit_ttl_bars=int(self.limit_ttl.value or 1),
                taker_fee_pct=float(self.taker_fee.value or 0),
                maker_fee_pct=_optional(self.maker_fee),
                slippage_bps=float(self.slippage.value or 0),
                spread_bps=float(self.spread.value or 0),
                funding_mode=self.funding_mode.value,
                funding_rate_pct=float(self.funding_rate.value or 0),
                intrabar_resolution=bool(self.intrabar.value),
            ),
            risk=RiskConfig(
                sizing_mode=self.sizing_mode.value,
                risk_per_trade_pct=_optional(self.risk_pct),
                notional_pct_of_equity=float(self.notional_pct.value or 100),
                max_leverage=float(self.leverage.value or 0),
                max_position_pct_of_equity=_optional(self.max_position_pct),
                risk_includes_costs=bool(self.risk_includes_costs.value),
            ),
            validation=ValidationConfig(
                mc_method=self.mc_method.value,
                mc_sims=int(self.mc_sims.value or 0),
                mc_seed=None if seed in (None, "") else int(seed),
                mc_block_size=int(self.mc_block.value or 1),
                mc_ruin_threshold_pct=float(self.mc_ruin.value or 20),
            ),
        )
