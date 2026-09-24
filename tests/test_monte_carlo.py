import datetime as dt

import numpy as np
import pytest

from app.backtest.engine import TradeRecord
from app.backtest.monte_carlo import _paths_stats, run_monte_carlo

T0 = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)


def _trades(returns) -> list[TradeRecord]:
    return [
        TradeRecord(side="long", entry_time=T0, entry_price=100.0, qty=1.0, equity_before=1000.0,
                    exit_time=T0 + dt.timedelta(hours=1), pnl=r * 1000.0, pnl_pct=r)
        for r in returns
    ]


def _reference_path(returns) -> tuple[float, float]:
    """Implementacion ingenua con un bucle: el oraculo contra el que se compara la vectorizada."""
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in returns:
        equity *= max(1 + r, 0.0)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, equity / peak - 1)
    return (equity - 1) * 100, max_dd * 100


def test_vectorized_paths_match_a_naive_loop() -> None:
    rng = np.random.default_rng(3)
    sample = rng.normal(0.002, 0.03, size=(50, 40))
    final, dd = _paths_stats(sample)
    for i in range(50):
        ref_final, ref_dd = _reference_path(sample[i])
        assert final[i] == pytest.approx(ref_final) and dd[i] == pytest.approx(ref_dd)


# ------------------------------------------------------------------ shuffle

def test_shuffle_keeps_final_return_and_only_changes_drawdown() -> None:
    mc = run_monte_carlo(_trades([-0.1, -0.1, 0.5]), 1000.0, n_sims=6000, method="shuffle", seed=1, min_trades=2)
    assert mc.return_pct_p5 == pytest.approx(21.5) and mc.return_pct_p95 == pytest.approx(21.5)
    assert mc.actual_return_pct == pytest.approx(21.5)  # 0.9 * 0.9 * 1.5 - 1

    # 6 ordenes posibles: 4 dan drawdown -19% y 2 dan -10% (enumerado a mano)
    dds = np.round(mc.drawdowns_distribution, 6)
    assert set(dds) == {-19.0, -10.0}
    assert (dds == -10.0).mean() == pytest.approx(2 / 6, abs=0.03)
    assert mc.actual_max_dd_pct == pytest.approx(-19.0)
    assert mc.actual_dd_worse_than_pct == pytest.approx(2 / 6 * 100, abs=3)  # solo los -10% son mas suaves


def test_shuffle_final_return_is_the_compounded_product() -> None:
    returns = [0.02, -0.01, 0.03, -0.02, 0.01, 0.015, -0.005, 0.02, -0.01, 0.01]
    mc = run_monte_carlo(_trades(returns), 1000.0, n_sims=500, method="shuffle", seed=5)
    assert mc.return_pct_p50 == pytest.approx((np.prod(1 + np.array(returns)) - 1) * 100)
    assert mc.return_pct_p5 == pytest.approx(mc.return_pct_p95)


# ---------------------------------------------------------------- bootstrap

def test_bootstrap_varies_final_return_when_trades_differ() -> None:
    returns = [0.05, -0.03, 0.02, -0.04, 0.06, -0.02, 0.03, -0.01, 0.04, -0.05]
    mc = run_monte_carlo(_trades(returns), 1000.0, n_sims=2000, method="bootstrap", seed=2)
    assert mc.return_pct_p5 < mc.return_pct_p50 < mc.return_pct_p95


def test_bootstrap_with_identical_trades_has_no_variation() -> None:
    mc = run_monte_carlo(_trades([0.01] * 10), 1000.0, n_sims=300, method="bootstrap", seed=2)
    expected = (1.01**10 - 1) * 100
    assert mc.return_pct_p5 == pytest.approx(expected) and mc.return_pct_p95 == pytest.approx(expected)


# ---------------------------------------------------------- block bootstrap

def test_block_bootstrap_with_full_length_blocks_only_rotates_the_sequence() -> None:
    mc = run_monte_carlo(_trades([-0.1, -0.1, 0.5]), 1000.0, n_sims=3000, method="block_bootstrap",
                         seed=4, block_size=3, min_trades=2)
    assert mc.return_pct_p5 == pytest.approx(21.5) and mc.return_pct_p95 == pytest.approx(21.5)
    assert set(np.round(mc.drawdowns_distribution, 6)) == {-19.0, -10.0}  # rotaciones: -19, -10, -19
    assert mc.block_size == 3


def test_block_bootstrap_preserves_runs_that_plain_bootstrap_breaks() -> None:
    """Con rachas (10 ganancias seguidas y 10 perdidas seguidas) el block bootstrap conserva rachas largas:
    su peor drawdown es mucho peor que el del bootstrap i.i.d., que las rompe."""
    returns = [0.01] * 10 + [-0.02] * 10
    block = run_monte_carlo(_trades(returns), 1000.0, n_sims=2000, method="block_bootstrap", seed=6, block_size=10)
    iid = run_monte_carlo(_trades(returns), 1000.0, n_sims=2000, method="bootstrap", seed=6)
    assert block.max_dd_p5 < iid.max_dd_p5


# ------------------------------------------------------------ reproducibility

def test_same_seed_reproduces_and_different_seed_does_not() -> None:
    returns = [0.05, -0.03, 0.02, -0.04, 0.06, -0.02, 0.03, -0.01, 0.04, -0.05]
    a = run_monte_carlo(_trades(returns), 1000.0, n_sims=500, method="bootstrap", seed=123)
    b = run_monte_carlo(_trades(returns), 1000.0, n_sims=500, method="bootstrap", seed=123)
    c = run_monte_carlo(_trades(returns), 1000.0, n_sims=500, method="bootstrap", seed=124)
    assert a.returns_distribution == b.returns_distribution and a.seed == 123
    assert a.returns_distribution != c.returns_distribution


def test_unseeded_run_records_the_seed_so_it_can_be_reproduced() -> None:
    returns = [0.05, -0.03, 0.02, -0.04, 0.06, -0.02, 0.03, -0.01, 0.04, -0.05]
    first = run_monte_carlo(_trades(returns), 1000.0, n_sims=300, method="bootstrap")
    again = run_monte_carlo(_trades(returns), 1000.0, n_sims=300, method="bootstrap", seed=first.seed)
    assert first.returns_distribution == again.returns_distribution


# ------------------------------------------------------- statistics / limits

def test_percentiles_are_ordered_and_probabilities_are_consistent() -> None:
    returns = [0.05, -0.03, 0.02, -0.04, 0.06, -0.02, 0.03, -0.01, 0.04, -0.05]
    mc = run_monte_carlo(_trades(returns), 1000.0, n_sims=2000, method="bootstrap", seed=9, ruin_threshold_pct=5)
    assert mc.return_pct_p5 <= mc.return_pct_p25 <= mc.return_pct_p50 <= mc.return_pct_p75 <= mc.return_pct_p95
    assert mc.max_dd_p5 <= mc.max_dd_p50 <= mc.max_dd_p95 <= 0
    assert mc.prob_loss_pct == pytest.approx((np.array(mc.returns_distribution) < 0).mean() * 100)
    assert mc.prob_ruin_pct == pytest.approx((np.array(mc.drawdowns_distribution) < -5).mean() * 100)


def test_all_losing_trades_means_certain_loss_and_ruin_above_threshold() -> None:
    mc = run_monte_carlo(_trades([-0.05] * 10), 1000.0, n_sims=200, method="shuffle", seed=1)
    assert mc.prob_loss_pct == 100.0 and mc.prob_ruin_pct == 100.0  # 0.95**10 -> -40% > 20%


def test_a_total_loss_leaves_zero_equity_and_stays_there() -> None:
    mc = run_monte_carlo(_trades([-1.5] + [0.1] * 9), 1000.0, n_sims=200, method="shuffle", seed=1)
    assert mc.return_pct_p50 == pytest.approx(-100.0) and mc.max_dd_p5 == pytest.approx(-100.0)


def test_too_few_trades_returns_none_and_small_samples_warn() -> None:
    assert run_monte_carlo(_trades([0.01] * 9), 1000.0, n_sims=100) is None
    mc = run_monte_carlo(_trades([0.01, -0.005] * 6), 1000.0, n_sims=100, seed=1)
    assert any("poco confiable" in w for w in mc.warnings)


def test_invalid_arguments_are_rejected() -> None:
    with pytest.raises(ValueError):
        run_monte_carlo(_trades([0.01] * 10), 1000.0, method="magia")
    with pytest.raises(ValueError):
        run_monte_carlo(_trades([0.01] * 10), 1000.0, n_sims=0)
    with pytest.raises(ValueError):
        run_monte_carlo(_trades([0.01] * 10), 1000.0, method="block_bootstrap", block_size=0)
