import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel, Field

from app.backtest.config import BacktestConfig, ExecutionConfig
from app.backtest.progress import Progress
from app.backtest.service import MarketData, simulate
from app.backtest.validation import (
    OptimizeSpec,
    make_windows,
    pick_by_train,
    run_in_out_sample,
    run_walk_forward,
    snap_split,
    split_from_fraction,
)
from app.strategies import registry
from app.strategies.base import Signal, Strategy, StrategyContext

T0 = pd.Timestamp("2024-01-01", tz="UTC")
SMA = registry.get("sma_cross")
CONFIG = BacktestConfig(timeframe="60", initial_capital=1000.0, execution=ExecutionConfig(taker_fee_pct=0.0))


def _market(n=3600, seed=8) -> MarketData:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.concatenate(([100.0], close[:-1]))
    idx = pd.DatetimeIndex([T0 + i * pd.Timedelta(hours=1) for i in range(n)], name="timestamp")
    df = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.001,
                       "low": np.minimum(open_, close) * 0.999, "close": close, "volume": 1.0}, index=idx)
    return MarketData(candles=df, quality={}, start=idx[0], end=idx[-1])


SMA_PARAMS = SMA.params_model(fast_period=5, slow_period=20)


# ------------------------------------------------------------------ windows

def test_windows_slide_by_the_step_and_always_fit_inside_the_data() -> None:
    windows = make_windows(T0, T0 + pd.Timedelta(days=400), 100, 50, 50)
    assert len(windows) == 6
    first, second = windows[0], windows[1]
    assert (first.train_start, first.train_end, first.test_start, first.test_end) == (
        T0, T0 + pd.Timedelta(days=100), T0 + pd.Timedelta(days=100), T0 + pd.Timedelta(days=150))
    assert second.train_start == T0 + pd.Timedelta(days=50) and second.test_end == T0 + pd.Timedelta(days=200)
    assert windows[-1].test_end == T0 + pd.Timedelta(days=400)
    assert all(w.test_end <= T0 + pd.Timedelta(days=400) for w in windows)
    assert all(w.test_start == w.train_end for w in windows)  # sin hueco ni solape entre train y test


def test_non_overlapping_test_windows_are_contiguous() -> None:
    windows = make_windows(T0, T0 + pd.Timedelta(days=400), 100, 50, 50)
    for a, b in zip(windows, windows[1:]):
        assert b.test_start == a.test_end


def test_windows_edge_cases() -> None:
    assert make_windows(T0, T0 + pd.Timedelta(days=100), 90, 30, 10) == []  # no entra ninguna
    with pytest.raises(ValueError):
        make_windows(T0, T0 + pd.Timedelta(days=100), 0, 10, 10)
    with pytest.raises(ValueError):
        make_windows(T0, T0 + pd.Timedelta(days=100), 10, 10, -1)


def test_split_helpers() -> None:
    candles = _market(500).candles
    assert snap_split(candles, T0 + pd.Timedelta(minutes=90)) == T0 + pd.Timedelta(hours=2)  # primera vela >= corte
    with pytest.raises(ValueError):
        snap_split(candles, T0 + pd.Timedelta(days=999))
    assert split_from_fraction(candles, 0.7) == candles.index[350]
    with pytest.raises(ValueError):
        split_from_fraction(candles, 0.95)


# ------------------------------------------------------------ out of sample

def test_in_and_out_of_sample_use_disjoint_periods_and_start_from_the_initial_capital() -> None:
    market = _market()
    cut = split_from_fraction(market.candles, 0.7)
    in_seg, out_seg = run_in_out_sample(SMA, SMA_PARAMS, CONFIG, market, cut)

    assert in_seg.result.equity_curve.index.max() < cut <= out_seg.result.equity_curve.index.min()
    assert all(t.entry_time < cut for t in in_seg.result.trades)
    assert all(t.entry_time >= cut for t in out_seg.result.trades)
    assert in_seg.result.equity_curve.iloc[0] == pytest.approx(1000.0)
    assert out_seg.result.equity_curve.iloc[0] == pytest.approx(1000.0)
    assert in_seg.metrics["num_trades"] > 0 and out_seg.metrics["num_trades"] > 0


def test_out_of_sample_result_does_not_depend_on_what_happened_in_sample() -> None:
    """El tramo OOS arranca de cero: correrlo solo o junto con el in-sample da lo mismo."""
    market = _market()
    cut = split_from_fraction(market.candles, 0.6)
    _, out_a = run_in_out_sample(SMA, SMA_PARAMS, CONFIG, market, cut)
    _, direct = simulate(SMA, SMA_PARAMS, CONFIG, market, trade_start=cut, include_quality=False)
    assert out_a.metrics["total_return_pct"] == pytest.approx(direct["total_return_pct"])
    assert out_a.metrics["num_trades"] == direct["num_trades"]


def test_segments_that_are_too_short_are_rejected() -> None:
    market = _market(400)
    with pytest.raises(ValueError, match="al menos"):
        run_in_out_sample(SMA, SMA_PARAMS, CONFIG, market, market.candles.index[20])
    with pytest.raises(ValueError, match="al menos"):
        run_in_out_sample(SMA, SMA_PARAMS, CONFIG, market, market.candles.index[-20])


# ---------------------------------------------------- walk-forward: no leakage

class _SpyParams(BaseModel):
    k: int = Field(default=1)


class Spy(Strategy):
    """Registra hasta donde ve datos cada simulacion: si alguna viera el futuro de su tramo, el test lo delata."""

    key = "test_spy"
    display_name = "spy"
    params_model = _SpyParams
    instances: list = []

    def __init__(self, params):
        super().__init__(params)
        self.k = params.k
        self.min_seen = self.max_seen = None
        Spy.instances.append(self)

    def on_candle(self, ctx: StrategyContext):
        ts = ctx.candles.index[-1]
        self.min_seen = ts if self.min_seen is None else min(self.min_seen, ts)
        self.max_seen = ts if self.max_seen is None else max(self.max_seen, ts)
        if len(ctx.candles) % 40 == 0:
            return Signal(action="buy", stop_loss=float(ctx.candles["close"].iloc[-1]) * 0.9)
        return None


def test_fixed_params_walk_forward_never_lets_a_segment_see_data_beyond_its_end() -> None:
    market = _market(2400)
    Spy.instances = []
    result = run_walk_forward(Spy, _SpyParams(), CONFIG, market, training_days=30, testing_days=10, step_days=10)
    assert len(result.windows) >= 3
    for i, w in enumerate(x.window for x in result.windows):
        train, test = Spy.instances[2 * i], Spy.instances[2 * i + 1]
        assert w.train_start <= train.min_seen and train.max_seen < w.train_end
        assert w.test_start <= test.min_seen and test.max_seen < w.test_end


def test_reoptimization_chooses_parameters_using_only_the_training_window() -> None:
    market = _market(2400)
    Spy.instances = []
    spec = OptimizeSpec(axes=[("k", [1, 2, 3])], objective="total_return_pct", min_train_trades=0)
    result = run_walk_forward(Spy, _SpyParams(), CONFIG, market, 30, 10, 10, optimize=spec)
    per_window = 3 + 1  # 3 combinaciones sobre el entrenamiento + 1 evaluacion en el test
    for i, wr in enumerate(result.windows):
        group = Spy.instances[i * per_window:(i + 1) * per_window]
        for inst in group[:-1]:  # el ajuste de parametros: solo ve el entrenamiento
            assert wr.window.train_start <= inst.min_seen and inst.max_seen < wr.window.train_end
        test = group[-1]  # la unica evaluacion sobre datos nuevos
        assert wr.window.test_start <= test.min_seen and test.max_seen < wr.window.test_end


def test_pick_by_train_uses_the_objective_and_ignores_unusable_points() -> None:
    points = [
        {"params": {"a": 1}, "metrics": {"num_trades": 30, "sharpe_ratio": 1.0}},
        {"params": {"a": 2}, "metrics": {"num_trades": 30, "sharpe_ratio": 2.5}},
        {"params": {"a": 3}, "metrics": {"num_trades": 3, "sharpe_ratio": 9.0}},     # pocas operaciones
        {"params": {"a": 4}, "metrics": {"num_trades": 30, "sharpe_ratio": None}},   # no calculable
    ]
    assert pick_by_train(points, "sharpe_ratio", min_trades=10)["params"] == {"a": 2}
    assert pick_by_train(points, "sharpe_ratio", min_trades=100) is None
    assert pick_by_train([points[3]], "sharpe_ratio", min_trades=10) is None


# ---------------------------------------------------- walk-forward: aggregation

def test_non_overlapping_windows_are_stitched_into_one_compounded_curve() -> None:
    market = _market()
    result = run_walk_forward(SMA, SMA_PARAMS, CONFIG, market, training_days=40, testing_days=20, step_days=20)
    evaluated = [w for w in result.windows if w.test_result is not None]
    assert len(evaluated) >= 3 and not result.overlapping
    expected = (np.prod([1 + w.test_metrics["total_return_pct"] / 100 for w in evaluated]) - 1) * 100
    assert result.aggregate["compounded_return_pct"] == pytest.approx(expected)
    assert result.stitched_metrics["num_trades"] == sum(w.test_metrics["num_trades"] for w in evaluated)
    assert result.stitched_result.equity_curve.index.is_monotonic_increasing


def test_overlapping_windows_are_not_stitched_and_say_why() -> None:
    market = _market()
    result = run_walk_forward(SMA, SMA_PARAMS, CONFIG, market, training_days=40, testing_days=20, step_days=10)
    assert result.overlapping and result.stitched_metrics is None
    assert "compounded_return_pct" not in result.aggregate
    assert any("se solapan" in w for w in result.warnings)


def test_aggregate_statistics_describe_the_windows() -> None:
    market = _market()
    result = run_walk_forward(SMA, SMA_PARAMS, CONFIG, market, 40, 20, 20)
    returns = [w.test_metrics["total_return_pct"] for w in result.windows if w.test_result is not None]
    agg = result.aggregate
    assert agg["n_evaluated"] == len(returns)
    assert agg["return_min"] == min(returns) and agg["return_max"] == max(returns)
    assert agg["pct_profitable"] == pytest.approx(sum(r > 0 for r in returns) / len(returns) * 100)


def test_skipped_windows_are_reported_when_no_combination_trades_enough() -> None:
    market = _market(2400)
    spec = OptimizeSpec(axes=[("fast_period", [4, 6])], objective="sharpe_ratio", min_train_trades=10_000)
    result = run_walk_forward(SMA, SMA_PARAMS, CONFIG, market, 30, 10, 10, optimize=spec)
    assert result.aggregate["n_skipped"] == len(result.windows) > 0
    assert all(w.skipped_reason for w in result.windows)


# --------------------------------------------------------- progress / limits

def test_progress_counts_every_simulation_and_can_cancel() -> None:
    market = _market(2400)
    progress = Progress()
    result = run_walk_forward(SMA, SMA_PARAMS, CONFIG, market, 30, 10, 10, progress=progress)
    assert progress.total == progress.done == 2 * len(result.windows)

    stopped = Progress(cancelled=True)
    cancelled = run_walk_forward(SMA, SMA_PARAMS, CONFIG, market, 30, 10, 10, progress=stopped)
    assert cancelled.cancelled and cancelled.windows == []


def test_impossible_or_excessive_configurations_are_rejected_with_a_clear_message() -> None:
    market = _market(1200)  # 50 dias
    with pytest.raises(ValueError, match="No entra ninguna ventana"):
        run_walk_forward(SMA, SMA_PARAMS, CONFIG, market, 60, 30, 10)
    big = OptimizeSpec(axes=[("fast_period", list(range(2, 40))), ("slow_period", list(range(40, 60)))])
    with pytest.raises(ValueError):
        run_walk_forward(SMA, SMA_PARAMS, CONFIG, _market(2400), 30, 10, 10, optimize=big)
    with pytest.raises(ValueError):
        run_walk_forward(SMA, SMA_PARAMS, CONFIG, market, 10, 5, 5, optimize=OptimizeSpec(axes=[]))
