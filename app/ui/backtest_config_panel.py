"""Panel de configuracion del backtest, separado en:
A) parametros de la estrategia, B) riesgo, C) ejecucion, D) validacion."""

from typing import Callable

from nicegui import ui
from pydantic import BaseModel

from app.backtest.config import BacktestConfig, ExecutionConfig, RiskConfig, ValidationConfig
from app.strategies import registry
from app.strategies.base import Strategy
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
                self.days = _number("Historia (días)", 90, min=7, max=1500, step=1)
                self.capital = _number("Capital inicial (USD)", 1000.0, min=10).props("prefix=$")

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
                "Sin slippage, spread ni funding los resultados son optimistas: usá el análisis de costos para medir cuánto."
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

    def _rebuild_params(self) -> None:
        self._params_box.clear()
        with self._params_box:
            self._get_params = render_param_form(self.strategy_cls.params_model)

    def get_params(self) -> BaseModel:
        return self._get_params()

    # ---------------------------------------------------------------- config

    def collect(self) -> BacktestConfig:
        """Arma la configuracion desde los widgets (la valida quien la usa)."""
        seed = self.mc_seed.value
        return BacktestConfig(
            symbol=(self.symbol.value or "").strip().upper(),
            timeframe=self.timeframe.value,
            days=int(self.days.value or 0),
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
