import itertools

import pandas as pd

from app.backtest.engine import Backtester
from app.backtest.metrics import compute_metrics
from app.strategies.base import Strategy

MAX_COMBINATIONS = 300


def build_param_grid(param_ranges: dict[str, list]) -> list[dict]:
    """param_ranges: {"fast_period": [5, 10, 15], "slow_period": [30, 50]}
    -> lista de todas las combinaciones posibles (producto cartesiano)."""
    keys = list(param_ranges.keys())
    values_product = itertools.product(*(param_ranges[k] for k in keys))
    return [dict(zip(keys, combo)) for combo in values_product]


def run_grid_search(
    strategy_cls: type[Strategy],
    param_grid: list[dict],
    candles: pd.DataFrame,
    initial_capital: float,
    timeframe: str,
    fee_pct: float,
    rank_by: str = "sharpe_ratio",
    max_leverage: float = 10.0,
) -> list[dict]:
    if len(param_grid) > MAX_COMBINATIONS:
        raise ValueError(
            f"El barrido tiene {len(param_grid)} combinaciones, el maximo permitido es "
            f"{MAX_COMBINATIONS}. Reduci el rango o el paso de los parametros."
        )

    results = []
    backtester = Backtester(fee_pct=fee_pct, max_leverage=max_leverage)
    for params_dict in param_grid:
        params = strategy_cls.params_model(**params_dict)
        strategy = strategy_cls(params)
        result = backtester.run(strategy, candles, initial_capital)
        metrics = compute_metrics(result, timeframe)
        if "error" in metrics:
            continue
        results.append({"params": params_dict, **metrics})

    results.sort(key=lambda r: (r.get(rank_by) is not None, r.get(rank_by, 0)), reverse=True)
    return results
