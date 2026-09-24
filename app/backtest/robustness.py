"""Sensibilidad a parametros: como cambian los resultados cuando se mueven
uno o dos parametros de la estrategia, con todo lo demas fijo.

El objetivo es VER la estabilidad (zonas amplias donde la estrategia se
comporta parecido), no elegir un ganador: por eso nada aca devuelve "el mejor"
parametro ni ordena por rendimiento."""

from dataclasses import dataclass, field

import numpy as np
from pydantic.fields import FieldInfo

from app.backtest.config import BacktestConfig
from app.backtest.optimizer import MAX_COMBINATIONS, build_param_grid, run_grid_search
from app.backtest.service import MarketData
from app.strategies.base import Strategy

SENSITIVITY_METRICS = [
    "total_return_pct", "sharpe_ratio", "sortino_ratio", "max_drawdown_pct", "profit_factor", "expectancy",
]
# Valor de referencia que separa "bien" de "mal" en cada metrica (centro del heatmap).
NEUTRAL_VALUE = {
    "total_return_pct": 0.0, "sharpe_ratio": 0.0, "sortino_ratio": 0.0, "profit_factor": 1.0,
    "expectancy": 0.0, "max_drawdown_pct": None,
}


@dataclass
class SensitivityResult:
    axes: list[tuple[str, list]]  # 1 o 2 (nombre, valores)
    base_params: dict
    points: list[dict] = field(default_factory=list)  # {"params": {...}, <metricas>}

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.axes]

    def point(self, *values):
        wanted = dict(zip(self.names, values))
        for p in self.points:
            if all(p["params"][k] == v for k, v in wanted.items()):
                return p
        return None

    def value(self, metric: str, *values) -> float | None:
        p = self.point(*values)
        return None if p is None else p.get(metric)


def param_bounds(field_info: FieldInfo) -> tuple[float | None, float | None]:
    """Limites permitidos de un parametro numerico, segun sus restricciones pydantic."""
    lo = hi = None
    for meta in field_info.metadata:
        for attr in ("ge", "gt"):
            if getattr(meta, attr, None) is not None:
                lo = getattr(meta, attr)
        for attr in ("le", "lt"):
            if getattr(meta, attr, None) is not None:
                hi = getattr(meta, attr)
    return lo, hi


def numeric_params(strategy_cls: type[Strategy]) -> dict[str, FieldInfo]:
    return {n: f for n, f in strategy_cls.params_model.model_fields.items() if f.annotation in (int, float)}


def run_sensitivity(
    strategy_cls: type[Strategy],
    base_params: dict,
    axes: list[tuple[str, list]],
    config: BacktestConfig,
    market: MarketData,
) -> SensitivityResult:
    if not 1 <= len(axes) <= 2:
        raise ValueError("La sensibilidad se calcula sobre uno o dos parametros.")
    valid = numeric_params(strategy_cls)
    for name, values in axes:
        if name not in valid:
            raise ValueError(f"'{name}' no es un parametro numerico de la estrategia.")
        if not values:
            raise ValueError(f"El rango de '{name}' no genera ningun valor.")
    if len({name for name, _ in axes}) != len(axes):
        raise ValueError("Los dos parametros deben ser distintos.")

    combos = build_param_grid({name: values for name, values in axes})
    if len(combos) > MAX_COMBINATIONS:
        raise ValueError(f"Son {len(combos)} combinaciones (maximo {MAX_COMBINATIONS}): reduci el rango o aumenta el paso.")

    grid = [{**base_params, **combo} for combo in combos]
    points = run_grid_search(strategy_cls, grid, config, market, rank_by=None)
    return SensitivityResult(axes=axes, base_params=dict(base_params), points=points)


def heatmap_matrix(result: SensitivityResult, metric: str) -> tuple[list, list, list[list[float | None]]]:
    """(valores del eje x, valores del eje y, z[y][x]) para dos parametros."""
    if len(result.axes) != 2:
        raise ValueError("El heatmap necesita dos parametros.")
    (_, xs), (_, ys) = result.axes
    z = [[result.value(metric, x, y) for x in xs] for y in ys]
    return xs, ys, z


def neighbor_comparison(result: SensitivityResult, metric: str) -> dict | None:
    """Compara el punto actual (los parametros base) con sus vecinos inmediatos
    en la grilla: un pico aislado (el punto actual mucho mejor que su entorno)
    es una senal de sobreajuste. None si el punto base no esta en la grilla."""
    names = result.names
    if any(result.base_params.get(n) not in dict(result.axes)[n] for n in names):
        return None
    positions = [dict(result.axes)[n].index(result.base_params[n]) for n in names]
    neighbors = []
    for axis, pos in enumerate(positions):
        for delta in (-1, 1):
            idx = pos + delta
            values = list(result.axes[axis][1])
            if 0 <= idx < len(values):
                coords = [result.base_params[n] for n in names]
                coords[axis] = values[idx]
                v = result.value(metric, *coords)
                if v is not None:
                    neighbors.append(v)
    base = result.value(metric, *[result.base_params[n] for n in names])
    if base is None or not neighbors:
        return None
    return {
        "base": base, "neighbor_median": float(np.median(neighbors)),
        "neighbor_min": float(min(neighbors)), "neighbor_max": float(max(neighbors)),
        "difference": float(base - np.median(neighbors)), "n_neighbors": len(neighbors),
    }


def stability_summary(result: SensitivityResult) -> dict:
    """Estadisticas descriptivas de toda la grilla. No elige ni ordena."""
    def values(metric):
        return np.array([p[metric] for p in result.points if p.get(metric) is not None], dtype=float)

    returns, sharpe, dd = values("total_return_pct"), values("sharpe_ratio"), values("max_drawdown_pct")
    trades = np.array([p["num_trades"] for p in result.points], dtype=float)
    n = len(result.points)
    return {
        "n_combinations": n,
        "pct_positive_return": float((returns > 0).mean() * 100) if len(returns) else None,
        "return_min": float(returns.min()) if len(returns) else None,
        "return_median": float(np.median(returns)) if len(returns) else None,
        "return_max": float(returns.max()) if len(returns) else None,
        "sharpe_median": float(np.median(sharpe)) if len(sharpe) else None,
        "worst_drawdown": float(dd.min()) if len(dd) else None,
        "trades_min": int(trades.min()) if n else None,
        "trades_max": int(trades.max()) if n else None,
        "pct_few_trades": float((trades < 30).mean() * 100) if n else None,
    }
