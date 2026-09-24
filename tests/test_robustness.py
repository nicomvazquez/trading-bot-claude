import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.backtest.config import BacktestConfig, ExecutionConfig
from app.backtest.robustness import (
    SensitivityResult,
    heatmap_matrix,
    neighbor_comparison,
    numeric_params,
    param_bounds,
    run_sensitivity,
    stability_summary,
)
from app.backtest.service import MarketData
from app.strategies import registry

T0 = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
SMA = registry.get("sma_cross")


def _market(n=1200, seed=5) -> MarketData:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.concatenate(([100.0], close[:-1]))
    idx = pd.DatetimeIndex([T0 + i * dt.timedelta(hours=1) for i in range(n)], name="timestamp")
    df = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.001,
                       "low": np.minimum(open_, close) * 0.999, "close": close, "volume": 1.0}, index=idx)
    return MarketData(candles=df, quality={}, start=idx[0], end=idx[-1])


CONFIG = BacktestConfig(timeframe="60", execution=ExecutionConfig(taker_fee_pct=0.0))
BASE = {"fast_period": 10, "slow_period": 50, "risk_pct": 1.0, "stop_loss_pct": 2.0}


def test_one_parameter_sweep_keeps_the_others_fixed() -> None:
    result = run_sensitivity(SMA, BASE, [("fast_period", [5, 8, 10, 12])], CONFIG, _market())
    assert [p["params"]["fast_period"] for p in result.points] == [5, 8, 10, 12]
    assert all(p["params"]["slow_period"] == 50 and p["params"]["stop_loss_pct"] == 2.0 for p in result.points)
    assert all("sharpe_ratio" in p and "profit_factor" in p for p in result.points)


def test_two_parameter_sweep_builds_the_full_grid_and_a_consistent_heatmap() -> None:
    result = run_sensitivity(SMA, BASE, [("fast_period", [5, 10]), ("slow_period", [30, 50, 70])], CONFIG, _market())
    assert len(result.points) == 6
    xs, ys, z = heatmap_matrix(result, "total_return_pct")
    assert xs == [5, 10] and ys == [30, 50, 70] and len(z) == 3 and len(z[0]) == 2
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            expected = next(p for p in result.points if p["params"]["fast_period"] == x and p["params"]["slow_period"] == y)
            assert z[j][i] == expected["total_return_pct"]


def test_heatmap_needs_exactly_two_parameters() -> None:
    result = run_sensitivity(SMA, BASE, [("fast_period", [5, 10])], CONFIG, _market())
    with pytest.raises(ValueError):
        heatmap_matrix(result, "total_return_pct")


def test_invalid_axes_are_rejected() -> None:
    market = _market()
    with pytest.raises(ValueError):
        run_sensitivity(SMA, BASE, [("nope", [1, 2])], CONFIG, market)
    with pytest.raises(ValueError):
        run_sensitivity(SMA, BASE, [("fast_period", [5]), ("slow_period", [30]), ("risk_pct", [1.0])], CONFIG, market)
    with pytest.raises(ValueError):
        run_sensitivity(SMA, BASE, [("fast_period", [5]), ("fast_period", [6])], CONFIG, market)
    with pytest.raises(ValueError):
        run_sensitivity(SMA, BASE, [("fast_period", [])], CONFIG, market)
    with pytest.raises(ValueError):  # demasiadas combinaciones
        run_sensitivity(SMA, BASE, [("fast_period", list(range(2, 40))), ("slow_period", list(range(50, 100)))], CONFIG, market)


def _synthetic(values, base) -> SensitivityResult:
    points = [{"params": {"x": x}, "total_return_pct": v, "sharpe_ratio": v / 10, "max_drawdown_pct": -abs(v),
               "num_trades": 40} for x, v in zip([1, 2, 3, 4, 5], values)]
    return SensitivityResult(axes=[("x", [1, 2, 3, 4, 5])], base_params={"x": base}, points=points)


def test_neighbor_comparison_flags_an_isolated_peak() -> None:
    peak = _synthetic([10, 20, 100, 20, 10], base=3)
    cmp = neighbor_comparison(peak, "total_return_pct")
    assert cmp["base"] == 100 and cmp["neighbor_median"] == 20 and cmp["difference"] == 80

    plateau = _synthetic([90, 95, 100, 98, 92], base=3)
    assert neighbor_comparison(plateau, "total_return_pct")["difference"] == pytest.approx(100 - 96.5)


def test_neighbor_comparison_at_the_edge_and_outside_the_grid() -> None:
    edge = _synthetic([10, 20, 30, 40, 50], base=1)
    assert neighbor_comparison(edge, "total_return_pct")["n_neighbors"] == 1  # en el borde solo hay un vecino
    assert neighbor_comparison(_synthetic([1, 2, 3, 4, 5], base=99), "total_return_pct") is None


def test_stability_summary_describes_the_whole_grid_without_picking_a_winner() -> None:
    summary = stability_summary(_synthetic([-10, 5, 20, 30, -5], base=3))
    assert summary["n_combinations"] == 5
    assert summary["pct_positive_return"] == pytest.approx(60.0)
    assert summary["return_min"] == -10 and summary["return_max"] == 30 and summary["return_median"] == 5
    assert summary["pct_few_trades"] == 0.0
    assert not any("best" in k or "mejor" in k or "argmax" in k for k in summary)


def test_param_bounds_and_numeric_params_come_from_the_strategy_schema() -> None:
    fields = numeric_params(SMA)
    assert set(fields) == {"fast_period", "slow_period", "risk_pct", "stop_loss_pct"}
    assert param_bounds(fields["fast_period"]) == (2, 200)
    assert param_bounds(fields["risk_pct"]) == (0.1, 10.0)
