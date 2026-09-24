import itertools

from app.backtest.config import BacktestConfig
from app.backtest.service import MarketData, simulate
from app.strategies.base import Strategy

MAX_COMBINATIONS = 300


def expand_range(low: float | None, high: float | None, step: float | None, is_int: bool) -> list:
    """Valores de un parametro para el barrido. Si min == max (o el paso no
    sirve) queda fijo en ese valor."""
    if low is None:
        return []

    def cast(v: float):
        return int(round(v)) if is_int else round(v, 6)

    if high is None or high <= low or not step or step <= 0:
        return [cast(low)]

    values = []
    v = low
    while v <= high + 1e-9 and len(values) < 10_000:
        values.append(cast(v))
        v += step
    return list(dict.fromkeys(values))


def build_param_grid(param_ranges: dict[str, list]) -> list[dict]:
    """param_ranges: {"fast_period": [5, 10, 15], "slow_period": [30, 50]}
    -> lista de todas las combinaciones posibles (producto cartesiano)."""
    keys = list(param_ranges.keys())
    values_product = itertools.product(*(param_ranges[k] for k in keys))
    return [dict(zip(keys, combo)) for combo in values_product]


def run_grid_search(
    strategy_cls: type[Strategy],
    param_grid: list[dict],
    config: BacktestConfig,
    market: MarketData,
    rank_by: str | None = None,
) -> list[dict]:
    """Una simulacion por combinacion, todas con la MISMA configuracion de
    ejecucion y riesgo. Si `rank_by` es None el orden es el de la grilla: el
    objetivo es ver estabilidad, no elegir un ganador."""
    if len(param_grid) > MAX_COMBINATIONS:
        raise ValueError(
            f"El barrido tiene {len(param_grid)} combinaciones, el maximo permitido es "
            f"{MAX_COMBINATIONS}. Reduci el rango o aumenta el paso."
        )

    results = []
    for params_dict in param_grid:
        params = strategy_cls.params_model(**params_dict)
        _, metrics = simulate(strategy_cls, params, config, market, include_quality=False)
        if "error" in metrics:
            continue
        results.append({"params": params_dict, **metrics})

    if rank_by:
        results.sort(key=lambda r: (r.get(rank_by) is not None, r.get(rank_by) or 0), reverse=True)
    return results
