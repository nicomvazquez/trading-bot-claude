"""Sensibilidad a costos y stress tests.

Ambos responden lo mismo: ¿cuanto de la ganancia sobrevive cuando la realidad es peor que el modelo? Cada punto es
una simulacion COMPLETA con el mismo motor y la misma estrategia (no un ajuste aproximado sobre el resultado base), asi
que las cifras son directamente comparables con el backtest original.

- Sensibilidad a costos: barre un costo por vez (slippage, comisiones, funding) y un cruce slippage x comisiones,
  y estima el punto de equilibrio donde la estrategia deja de ganar.
- Stress tests: escenarios fijos y nombrados (costos duplicados, stops con gap, funding adverso, mas volatilidad y una
  combinacion de todo).

No se elige ningun ganador ni se ordena por rendimiento: es un analisis de fragilidad, no de optimizacion."""

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
from pydantic import BaseModel

from app.backtest.config import BacktestConfig
from app.backtest.progress import Progress
from app.backtest.service import MarketData, simulate
from app.strategies.base import Strategy

SLIPPAGE_POINTS = [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]          # bps
FEE_MULTIPLIERS = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]                   # x las comisiones configuradas
HEAT_SLIPPAGE = [0.0, 2.0, 5.0, 10.0, 20.0]                         # bps
HEAT_FEES = [0.5, 1.0, 2.0, 3.0]                                    # x comisiones
CONSTANT_FUNDING_POINTS = [0.01, 0.03]                              # % por 8 h
STRESS_SLIPPAGE_FLOOR_BPS = 5.0   # si el slippage configurado es 0, "x2" y "x3" parten de esta referencia
ADVERSE_CONSTANT_FUNDING_PCT = 0.03  # funding adverso sin historico disponible (% por 8 h)

KEY_METRICS = ["total_return_pct", "sharpe_ratio", "max_drawdown_pct", "profit_factor", "final_equity", "num_trades", "expectancy"]


# ------------------------------------------------------------------ transformaciones

def with_execution(config: BacktestConfig, **changes) -> BacktestConfig:
    return replace(config, execution=replace(config.execution, **changes))


def scale_fees(config: BacktestConfig, multiplier: float) -> BacktestConfig:
    """Multiplica las comisiones taker y maker (si no habia maker propia, se parte de la taker)."""
    e = config.execution
    return with_execution(config, taker_fee_pct=e.taker_fee_pct * multiplier, maker_fee_pct=e.effective_maker_fee_pct * multiplier)


def scale_volatility(candles: pd.DataFrame, k: float) -> pd.DataFrame:
    """Mercado sintetico donde cada retorno de vela es `k` veces el original (k=2: el doble de volatilidad).
    Los retornos de cierre a cierre se multiplican exactamente por k en escala logaritmica; el maximo y el minimo de
    cada vela se escalan igual respecto de su apertura y se corrigen para que la vela siga siendo valida. El volumen y
    cualquier otra columna (funding, open interest) no se tocan. Con k=1 devuelve una copia identica."""
    out = candles.copy()
    if k == 1:
        return out
    o, h, l, c = (candles[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    log_ret = np.diff(np.log(c), prepend=np.log(o[0]))
    new_close = np.exp(np.log(o[0]) + k * np.cumsum(log_ret))
    new_open = np.concatenate(([o[0]], new_close[:-1]))
    up, down = np.log(h / o), np.log(l / o)
    new_high = np.maximum.reduce([new_open * np.exp(k * up), new_open, new_close])
    new_low = np.minimum.reduce([new_open * np.exp(k * down), new_open, new_close])
    out["open"], out["high"], out["low"], out["close"] = new_open, new_high, new_low, new_close
    return out


# ------------------------------------------------------------------ punto de equilibrio

def break_even(xs: list[float], returns: list[float | None]) -> tuple[float | None, str]:
    """Valor de `xs` donde el retorno cruza de >= 0 a < 0, por interpolacion lineal entre puntos.
    Devuelve (valor, explicacion). Valor None si no hay cruce dentro del rango probado."""
    pairs = [(x, r) for x, r in zip(xs, returns, strict=True) if r is not None]
    if len(pairs) < 2:
        return None, "No hay suficientes puntos para estimarlo."
    if pairs[0][1] < 0:
        return None, "Ya pierde con el valor más bajo probado."
    for (x0, r0), (x1, r1) in zip(pairs, pairs[1:], strict=False):
        if r0 >= 0 > r1:
            return x0 + (x1 - x0) * r0 / (r0 - r1), "Estimado por interpolación entre dos puntos probados."
    return None, f"No se anula dentro del rango probado (hasta {pairs[-1][0]:g})."


# ------------------------------------------------------------------ ejecucion de un punto

def _key_metrics(metrics: dict) -> dict:
    return {k: metrics.get(k) for k in KEY_METRICS} | {"warnings": metrics.get("warnings", [])}


def _run(strategy_cls: type[Strategy], params: BaseModel, config: BacktestConfig, market: MarketData,
         funding: pd.Series | None = None) -> tuple[dict | None, str | None]:
    """Una simulacion completa. Devuelve (metricas clave, error)."""
    try:
        if config.execution.funding_mode == "historical":
            market = replace(market, funding=funding if funding is not None else market.funding)
        _, metrics = simulate(strategy_cls, params, config, market, include_quality=False)
        return _key_metrics(metrics), None
    except Exception as exc:  # noqa: BLE001 - un punto que falla no debe tirar todo el analisis
        return None, str(exc)


@dataclass
class CostPoint:
    label: str
    x: float | None
    metrics: dict | None
    error: str | None = None


@dataclass
class CostSensitivity:
    slippage: list[CostPoint] = field(default_factory=list)
    fees: list[CostPoint] = field(default_factory=list)
    funding: list[CostPoint] = field(default_factory=list)
    heat_x: list[float] = field(default_factory=list)
    heat_y: list[float] = field(default_factory=list)
    heat_z: list[list[float | None]] = field(default_factory=list)
    break_even_slippage: tuple[float | None, str] = (None, "")
    break_even_fees: tuple[float | None, str] = (None, "")
    notes: list[str] = field(default_factory=list)


def _funding_points(has_history: bool) -> list[tuple[str, dict]]:
    points = [("Sin funding", {"funding_mode": "none", "funding_adverse": False})]
    points += [(f"Constante {r:g}%/8h", {"funding_mode": "constant", "funding_rate_pct": r, "funding_adverse": False}) for r in CONSTANT_FUNDING_POINTS]
    if has_history:
        points += [
            ("Histórico real", {"funding_mode": "historical", "funding_adverse": False}),
            ("Histórico, siempre en contra", {"funding_mode": "historical", "funding_adverse": True}),
        ]
    return points


def cost_sensitivity_sim_count(has_funding_history: bool) -> int:
    return len(SLIPPAGE_POINTS) + len(FEE_MULTIPLIERS) + len(_funding_points(has_funding_history)) + len(HEAT_SLIPPAGE) * len(HEAT_FEES)


def run_cost_sensitivity(strategy_cls: type[Strategy], params: BaseModel, config: BacktestConfig, market: MarketData,
                         funding: pd.Series | None = None, progress: Progress | None = None) -> CostSensitivity:
    """Barre los costos uno por vez y en cruce. `funding`: serie historica real (None si no se pudo obtener)."""
    has_history = funding is not None and not funding.empty
    progress = progress or Progress()
    progress.total = cost_sensitivity_sim_count(has_history)
    result = CostSensitivity()
    if not has_history:
        result.notes.append("No hay funding histórico disponible: solo se probó funding constante.")

    def run(label: str, x: float | None, cfg: BacktestConfig) -> CostPoint:
        progress.check()
        metrics, error = _run(strategy_cls, params, cfg, market, funding)
        progress.step(f"Probando {label}")
        return CostPoint(label, x, metrics, error)

    for bps in SLIPPAGE_POINTS:
        result.slippage.append(run(f"slippage {bps:g} bps", bps, with_execution(config, slippage_bps=bps)))
    for m in FEE_MULTIPLIERS:
        result.fees.append(run(f"comisiones ×{m:g}", m, scale_fees(config, m)))
    for label, changes in _funding_points(has_history):
        result.funding.append(run(label, None, with_execution(config, **changes)))

    result.heat_x, result.heat_y = list(HEAT_SLIPPAGE), list(HEAT_FEES)
    for m in HEAT_FEES:
        row = []
        for bps in HEAT_SLIPPAGE:
            point = run(f"slippage {bps:g} bps con comisiones ×{m:g}", None, with_execution(scale_fees(config, m), slippage_bps=bps))
            row.append(point.metrics["total_return_pct"] if point.metrics else None)
        result.heat_z.append(row)

    result.break_even_slippage = break_even(SLIPPAGE_POINTS, [p.metrics["total_return_pct"] if p.metrics else None for p in result.slippage])
    result.break_even_fees = break_even(FEE_MULTIPLIERS, [p.metrics["total_return_pct"] if p.metrics else None for p in result.fees])
    return result


# ------------------------------------------------------------------ stress tests

@dataclass
class Scenario:
    key: str
    label: str
    description: str
    config: BacktestConfig
    volatility: float = 1.0


@dataclass
class StressResult:
    key: str
    label: str
    description: str
    metrics: dict | None
    error: str | None = None


def build_scenarios(config: BacktestConfig, has_funding_history: bool) -> list[Scenario]:
    """Escenarios fijos. El slippage configurado se multiplica; si es 0 se parte de una referencia de 5 bps para
    que el escenario tenga sentido."""
    ref = max(config.execution.slippage_bps, STRESS_SLIPPAGE_FLOOR_BPS)
    adverse_funding = (
        {"funding_mode": "historical", "funding_adverse": True} if has_funding_history
        else {"funding_mode": "constant", "funding_rate_pct": ADVERSE_CONSTANT_FUNDING_PCT, "funding_adverse": True}
    )
    funding_text = "el funding histórico real, siempre pagándolo" if has_funding_history else f"un funding constante de {ADVERSE_CONSTANT_FUNDING_PCT:g}% cada 8 h, siempre pagándolo"
    worst = with_execution(scale_fees(config, 2.0), slippage_bps=ref * 3, stop_slippage_bps=100.0, **adverse_funding)
    return [
        Scenario("base", "Escenario base", "La configuración tal como la corriste.", config),
        Scenario("fees_x2", "Comisiones ×2", "Las comisiones taker y maker se duplican.", scale_fees(config, 2.0)),
        Scenario("slip_x2", "Slippage ×2", f"El slippage se duplica (referencia: {ref:g} bps → {ref * 2:g} bps).", with_execution(config, slippage_bps=ref * 2)),
        Scenario("slip_x3", "Slippage ×3", f"El slippage se triplica (referencia: {ref:g} bps → {ref * 3:g} bps).", with_execution(config, slippage_bps=ref * 3)),
        Scenario("funding_adverse", "Funding adverso", f"Se usa {funding_text}, sea la posición larga o corta.", with_execution(config, **adverse_funding)),
        Scenario("vol_x1_5", "Volatilidad ×1,5", "Mercado sintético con cada retorno de vela 1,5 veces mayor. La estrategia decide sobre esos precios.", config, 1.5),
        Scenario("vol_x2", "Volatilidad ×2", "Mercado sintético con cada retorno de vela el doble de grande.", config, 2.0),
        Scenario("stop_gap_1", "Stops con gap de 1%", "Cada stop-loss se ejecuta 1% peor que su nivel (saltos de precio en eventos de mercado).", with_execution(config, stop_slippage_bps=100.0)),
        Scenario("stop_gap_3", "Stops con gap de 3%", "Cada stop-loss se ejecuta 3% peor que su nivel.", with_execution(config, stop_slippage_bps=300.0)),
        Scenario("worst", "Peor caso combinado", f"Comisiones ×2, slippage ×3 ({ref * 3:g} bps), funding adverso y stops con gap de 1%, todo junto.", worst),
    ]


def stress_sim_count(config: BacktestConfig, has_funding_history: bool) -> int:
    return len(build_scenarios(config, has_funding_history))


def run_stress_tests(strategy_cls: type[Strategy], params: BaseModel, config: BacktestConfig, market: MarketData,
                     funding: pd.Series | None = None, progress: Progress | None = None) -> list[StressResult]:
    has_history = funding is not None and not funding.empty
    scenarios = build_scenarios(config, has_history)
    progress = progress or Progress()
    progress.total = len(scenarios)
    results = []
    for sc in scenarios:
        progress.check()
        scenario_market = market
        if sc.volatility != 1.0:
            # los precios cambian: las velas de menor timeframe (intravela) ya no coinciden y se descartan
            scenario_market = replace(market, candles=scale_volatility(market.candles, sc.volatility), intrabar=None)
        metrics, error = _run(strategy_cls, params, sc.config, scenario_market, funding)
        progress.step(f"Probando {sc.label}")
        results.append(StressResult(sc.key, sc.label, sc.description, metrics, error))
    return results
