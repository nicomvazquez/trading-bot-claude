"""Formulas verificadas con casos que se calculan a mano."""

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from app.backtest.engine import BacktestResult, TradeRecord
from app.backtest.metrics import (
    compute_metrics,
    drawdown_episodes,
    drawdown_series,
    historical_var_cvar,
    sharpe_ratio,
    sortino_ratio,
)

T0 = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
HOUR = pd.Timedelta(hours=1)


def _times(n: int, step: pd.Timedelta = HOUR) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([T0 + i * step for i in range(n)])


def _result(values, initial=100.0, trades=None, exposure=None, step=HOUR) -> BacktestResult:
    idx = _times(len(values), step)
    return BacktestResult(
        "s", "BTCUSDT", "60", {}, initial, pd.Series(values, index=idx, dtype=float),
        trades or [], exposure=pd.Series(exposure, index=idx) if exposure is not None else None,
        bar_seconds=step.total_seconds(),
    )


def _trade(pnl, side="long", hours=2, equity_before=1000.0) -> TradeRecord:
    return TradeRecord(
        side=side, entry_time=T0, entry_price=100.0, qty=1.0, equity_before=equity_before,
        exit_time=T0 + dt.timedelta(hours=hours), exit_price=100.0 + pnl, pnl=pnl, gross_pnl=pnl,
        pnl_pct=pnl / equity_before, exit_reason="senal",
    )


# ------------------------------------------------------------------ drawdown

def test_max_drawdown_known_case() -> None:
    dd = drawdown_series(np.array([110.0, 99.0, 105.0, 120.0]), initial=100.0)
    np.testing.assert_allclose(dd, [0.0, 99 / 110 - 1, 105 / 110 - 1, 0.0])
    assert dd.min() == pytest.approx(-0.1)


def test_drawdown_uses_initial_capital_as_first_peak() -> None:
    dd = drawdown_series(np.array([90.0, 95.0, 100.0]), initial=100.0)
    np.testing.assert_allclose(dd, [-0.1, -0.05, 0.0])


def test_drawdown_duration_is_peak_to_recovery() -> None:
    values = np.array([100.0, 110.0, 99.0, 105.0, 110.0, 120.0])  # pico en t1, recupera en t4
    dd = drawdown_series(values, 100.0)
    duration, recovered = drawdown_episodes(_times(len(values)), dd, HOUR)
    assert duration == pd.Timedelta(hours=3)
    assert recovered is True


def test_drawdown_duration_when_never_recovered() -> None:
    values = np.array([100.0, 110.0, 99.0, 105.0])
    dd = drawdown_series(values, 100.0)
    duration, recovered = drawdown_episodes(_times(len(values)), dd, HOUR)
    assert duration == pd.Timedelta(hours=2)  # de t1 (pico) a t3 (fin)
    assert recovered is False


# ------------------------------------------------------------ sharpe / sortino

RETURNS = np.array([0.01, -0.01, 0.02, 0.0])


def test_sharpe_matches_closed_form() -> None:
    # media 0.005; desvio muestral = sqrt(5e-4 / 3)
    expected = 0.005 / math.sqrt(5e-4 / 3) * math.sqrt(365)
    assert sharpe_ratio(RETURNS, 365) == pytest.approx(expected)


def test_sortino_uses_downside_deviation_over_all_observations() -> None:
    # solo un retorno negativo (-0.01): DD = sqrt(mean([0, 1e-4, 0, 0])) = 0.005
    # sortino = 0.005 / 0.005 * sqrt(365)
    assert sortino_ratio(RETURNS, 365) == pytest.approx(math.sqrt(365))


def test_sharpe_and_sortino_are_none_when_not_computable() -> None:
    assert sharpe_ratio(np.array([0.01, 0.01, 0.01]), 365) is None  # varianza cero
    assert sortino_ratio(np.array([0.01, 0.02, 0.0]), 365) is None  # sin retornos a la baja
    assert sharpe_ratio(np.array([0.01]), 365) is None  # una sola observacion


def test_sharpe_subtracts_risk_free_rate() -> None:
    base = sharpe_ratio(RETURNS, 365)
    assert sharpe_ratio(RETURNS, 365, rf_per_period=0.001) < base


# ------------------------------------------------------------------ var / cvar

def test_var_and_cvar_known_case() -> None:
    returns = np.array([-0.10] * 2 + [-0.04] * 8 + [0.01] * 90)
    var, cvar = historical_var_cvar(returns, 0.95)
    assert var == pytest.approx(0.04)
    assert cvar == pytest.approx((2 * 0.10 + 8 * 0.04) / 10)  # 0.052


def test_var_is_none_with_too_few_observations() -> None:
    assert historical_var_cvar(np.array([0.01, -0.02]), 0.95) == (None, None)


# ------------------------------------------------------------------------ cagr

def test_cagr_two_years_ten_percent_per_year() -> None:
    # 729.5 dias entre marcas + 1 dia de vela = 730.5 dias = 2 anos exactos
    idx = pd.DatetimeIndex([T0, T0 + pd.Timedelta(days=729.5)])
    result = BacktestResult(
        "s", "BTCUSDT", "D", {}, 1000.0, pd.Series([1000.0, 1210.0], index=idx),
        [], bar_seconds=86400.0,
    )
    metrics = compute_metrics(result, "D")
    assert metrics["cagr_pct"] == pytest.approx(10.0, abs=1e-6)
    assert metrics["total_return_pct"] == pytest.approx(21.0)
    assert metrics["cagr_representative"] is True


def test_short_period_warns_that_cagr_is_not_representative() -> None:
    metrics = compute_metrics(_result([100.0, 101.0, 102.0]), "60")
    texts = " ".join(w["text"] for w in metrics["warnings"])
    assert "Periodo muy corto" in texts
    assert metrics["cagr_representative"] is False


# ---------------------------------------------------------------------- trades

def test_trade_statistics_known_case() -> None:
    pnls = [100.0, -50.0, 30.0, -20.0, -10.0, 0.0]
    trades = [_trade(p, side="long" if i % 2 == 0 else "short") for i, p in enumerate(pnls)]
    metrics = compute_metrics(_result([100.0] * 10, trades=trades), "60")

    assert metrics["num_trades"] == 6
    assert metrics["win_rate_pct"] == pytest.approx(2 / 6 * 100)
    assert metrics["profit_factor"] == pytest.approx(130 / 80)
    assert metrics["avg_win"] == pytest.approx(65.0)
    assert metrics["avg_loss"] == pytest.approx(-80 / 3)
    assert metrics["payoff_ratio"] == pytest.approx(65.0 / (80 / 3))
    assert metrics["expectancy"] == pytest.approx(50 / 6)
    assert metrics["best_trade"] == 100.0
    assert metrics["worst_trade"] == -50.0
    assert metrics["median_trade"] == pytest.approx(-5.0)  # ordenadas: -50 -20 -10 0 30 100
    assert metrics["breakeven_trades"] == 1
    assert metrics["max_consecutive_wins"] == 1
    assert metrics["max_consecutive_losses"] == 2
    assert metrics["avg_trade_duration_hours"] == pytest.approx(2.0)
    assert metrics["long_trades"] == 3 and metrics["short_trades"] == 3


def test_profit_factor_is_none_without_losing_trades() -> None:
    metrics = compute_metrics(_result([100.0] * 10, trades=[_trade(10.0), _trade(5.0)]), "60")
    assert metrics["profit_factor"] is None
    assert metrics["avg_loss"] is None


def test_no_trades_does_not_crash_and_warns() -> None:
    metrics = compute_metrics(_result([100.0] * 10), "60")
    assert metrics["num_trades"] == 0
    assert metrics["win_rate_pct"] is None and metrics["expectancy"] is None
    assert any("no cerro ninguna operacion" in w["text"] for w in metrics["warnings"])


def test_few_trades_warning_levels() -> None:
    few = compute_metrics(_result([100.0] * 10, trades=[_trade(1.0)] * 5), "60")
    assert any("muestra insuficiente" in w["text"] for w in few["warnings"])
    small = compute_metrics(_result([100.0] * 10, trades=[_trade(1.0)] * 20), "60")
    assert any("muestra chica" in w["text"] for w in small["warnings"])


# -------------------------------------------------------------------- exposure

def test_exposure_split_long_short() -> None:
    metrics = compute_metrics(_result([100.0] * 5, exposure=[1, 1, 0, -1, 0]), "60")
    assert metrics["exposure_pct"] == pytest.approx(60.0)
    assert metrics["long_exposure_pct"] == pytest.approx(40.0)
    assert metrics["short_exposure_pct"] == pytest.approx(20.0)


def test_metrics_are_json_safe() -> None:
    import json

    metrics = compute_metrics(_result([100.0, 100.0, 100.0]), "60")  # sin variacion: sharpe None
    json.dumps(metrics, allow_nan=False)  # no debe contener NaN ni inf
    assert metrics["sharpe_ratio"] is None
