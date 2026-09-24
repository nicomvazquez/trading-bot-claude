import dataclasses

import numpy as np
import pandas as pd
import pytest

from app.backtest.config import BacktestConfig
from app.backtest.progress import Cancelled, Progress
from app.backtest.service import MarketData
from app.backtest.stress import (
    FEE_MULTIPLIERS, SLIPPAGE_POINTS, break_even, build_scenarios, cost_sensitivity_sim_count, run_cost_sensitivity,
    run_stress_tests, scale_fees, scale_volatility, with_execution,
)
from app.strategies import registry


def make_candles(n=1500, seed=5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    regime = np.repeat(rng.choice([-0.0007, 0.0, 0.0009], size=n // 250 + 1), 250)[:n]
    closes = 100 * np.exp(np.cumsum(regime + rng.normal(0, 0.004, n)))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": opens, "high": np.maximum(opens, closes) * 1.002, "low": np.minimum(opens, closes) * 0.998,
        "close": closes, "volume": 1.0,
    }, index=idx)


def market_for(candles: pd.DataFrame) -> MarketData:
    return MarketData(candles=candles, quality={}, start=candles.index[0].to_pydatetime(), end=candles.index[-1].to_pydatetime())


def funding_for(candles: pd.DataFrame) -> pd.Series:
    idx = pd.date_range(candles.index[0].ceil("8h"), candles.index[-1], freq="8h")
    return pd.Series(np.full(len(idx), 0.0001), index=idx)


DONCHIAN = registry.get("donchian_breakout")
CFG = BacktestConfig(timeframe="60")


# ------------------------------------------------------------------ transformaciones

def test_volatility_scaling_multiplies_log_returns_and_keeps_candles_valid() -> None:
    c = make_candles(400)
    for k in (0.5, 1.5, 2.0):
        s = scale_volatility(c, k)
        np.testing.assert_allclose(np.diff(np.log(s["close"])), k * np.diff(np.log(c["close"])), rtol=1e-9, atol=1e-12)
        assert (s["high"] >= s[["open", "close"]].max(axis=1) - 1e-12).all()
        assert (s["low"] <= s[["open", "close"]].min(axis=1) + 1e-12).all()
        assert (s["low"] > 0).all()
        assert s["open"].iloc[0] == c["open"].iloc[0] and (s["volume"] == c["volume"]).all() and s.index.equals(c.index)
    pd.testing.assert_frame_equal(scale_volatility(c, 1.0), c)  # k=1: copia identica


def test_volatility_scaling_leaves_extra_columns_untouched() -> None:
    c = make_candles(50)
    c["funding_rate"], c["open_interest"] = 0.0001, 500.0
    s = scale_volatility(c, 2.0)
    assert (s["funding_rate"] == 0.0001).all() and (s["open_interest"] == 500.0).all()
    assert s is not c and (c["close"] != s["close"]).any() and c["close"].iloc[5] == make_candles(50)["close"].iloc[5]  # no muta el original


def test_scale_fees_multiplies_taker_and_maker_and_defaults_maker_to_taker() -> None:
    cfg = scale_fees(CFG, 2.0)  # maker no configurada: parte de la taker
    assert cfg.execution.taker_fee_pct == pytest.approx(0.11) and cfg.execution.maker_fee_pct == pytest.approx(0.11)
    cfg2 = scale_fees(with_execution(CFG, maker_fee_pct=0.02), 3.0)
    assert cfg2.execution.maker_fee_pct == pytest.approx(0.06)
    assert CFG.execution.taker_fee_pct == 0.055  # la configuracion original no cambia


# ------------------------------------------------------------------ punto de equilibrio

def test_break_even_interpolates_between_the_last_winning_and_first_losing_point() -> None:
    value, note = break_even([0, 5, 10, 20], [10.0, 4.0, -2.0, -20.0])
    assert value == pytest.approx(5 + 5 * 4 / 6)
    assert "interpolación" in note


def test_break_even_handles_no_crossing_and_already_losing() -> None:
    assert break_even([0, 5, 10], [5.0, 3.0, 1.0])[0] is None and "hasta 10" in break_even([0, 5, 10], [5.0, 3.0, 1.0])[1]
    assert break_even([0, 5], [-1.0, -3.0])[0] is None and "Ya pierde" in break_even([0, 5], [-1.0, -3.0])[1]
    assert break_even([0], [1.0])[0] is None
    # un punto sin dato se saltea: interpola entre los vecinos con dato (0 -> 4.0 y 10 -> -2.0)
    assert break_even([0, 5, 10], [4.0, None, -2.0])[0] == pytest.approx(10 * 4 / 6)


# ------------------------------------------------------------------ escenarios

def test_scenarios_use_a_reference_when_configured_slippage_is_zero() -> None:
    sc = {s.key: s for s in build_scenarios(CFG, has_funding_history=True)}
    assert sc["slip_x2"].config.execution.slippage_bps == 10.0 and sc["slip_x3"].config.execution.slippage_bps == 15.0
    with_slip = {s.key: s for s in build_scenarios(with_execution(CFG, slippage_bps=8.0), True)}
    assert with_slip["slip_x2"].config.execution.slippage_bps == 16.0  # parte del valor configurado, no de la referencia
    assert sc["base"].config == CFG


def test_worst_case_combines_everything_and_funding_falls_back_to_constant_without_history() -> None:
    w = {s.key: s for s in build_scenarios(CFG, True)}["worst"].config.execution
    assert w.taker_fee_pct == pytest.approx(0.11) and w.slippage_bps == 15.0 and w.stop_slippage_bps == 100.0
    assert w.funding_mode == "historical" and w.funding_adverse
    nohist = {s.key: s for s in build_scenarios(CFG, False)}
    assert nohist["funding_adverse"].config.execution.funding_mode == "constant"
    assert nohist["funding_adverse"].config.execution.funding_rate_pct == 0.03 and "constante" in nohist["funding_adverse"].description


def test_scenarios_do_not_mutate_the_original_configuration() -> None:
    before = dataclasses.asdict(CFG)
    build_scenarios(CFG, True)
    assert dataclasses.asdict(CFG) == before


# ------------------------------------------------------------------ corridas reales en el motor

def test_stress_tests_run_end_to_end_and_costs_never_help() -> None:
    candles = make_candles()
    market, funding = market_for(candles), funding_for(candles)
    results = {r.key: r for r in run_stress_tests(DONCHIAN, DONCHIAN.params_model(), CFG, market, funding)}
    assert len(results) == 10 and all(r.error is None and r.metrics for r in results.values())
    base = results["base"].metrics
    assert base["num_trades"] > 5
    ret = lambda k: results[k].metrics["total_return_pct"]  # noqa: E731
    assert ret("fees_x2") < ret("base") and ret("slip_x3") < ret("slip_x2") < ret("base")
    assert ret("stop_gap_3") < ret("stop_gap_1") <= ret("base")
    assert ret("worst") <= min(ret("fees_x2"), ret("slip_x3"), ret("stop_gap_1"))  # combinar es peor que cada parte
    assert results["funding_adverse"].metrics["num_trades"] == base["num_trades"]  # el funding no cambia las decisiones


def test_base_scenario_equals_a_plain_simulation() -> None:
    from app.backtest.service import simulate

    candles = make_candles()
    market = market_for(candles)
    _, plain = simulate(DONCHIAN, DONCHIAN.params_model(), CFG, market, include_quality=False)
    base = run_stress_tests(DONCHIAN, DONCHIAN.params_model(), CFG, market, None)[0]
    assert base.metrics["total_return_pct"] == pytest.approx(plain["total_return_pct"])
    assert base.metrics["num_trades"] == plain["num_trades"]


def test_cost_sensitivity_is_monotonic_and_reports_the_break_even() -> None:
    candles = make_candles()
    market, funding = market_for(candles), funding_for(candles)
    progress = Progress()
    result = run_cost_sensitivity(DONCHIAN, DONCHIAN.params_model(), CFG, market, funding, progress)
    assert progress.total == progress.done == cost_sensitivity_sim_count(True)
    slip = [p.metrics["total_return_pct"] for p in result.slippage]
    fees = [p.metrics["total_return_pct"] for p in result.fees]
    assert all(a >= b - 1e-9 for a, b in zip(slip, slip[1:], strict=False))    # mas slippage nunca mejora
    assert all(a >= b - 1e-9 for a, b in zip(fees, fees[1:], strict=False))    # mas comisiones nunca mejora
    assert [p.x for p in result.slippage] == SLIPPAGE_POINTS and [p.x for p in result.fees] == FEE_MULTIPLIERS
    assert len(result.funding) == 5 and len(result.heat_z) == 4 and all(len(r) == 5 for r in result.heat_z)
    # el cruce (x1 comision, 0 slippage) del mapa de calor coincide con el punto de la barrida de slippage
    assert result.heat_z[1][0] == pytest.approx(slip[0])
    # el peor rincon del mapa de calor es peor que el mejor
    assert result.heat_z[-1][-1] < result.heat_z[0][0]


def test_cost_sensitivity_without_funding_history_only_tests_constant_funding() -> None:
    candles = make_candles(600)
    result = run_cost_sensitivity(DONCHIAN, DONCHIAN.params_model(), CFG, market_for(candles), None)
    assert len(result.funding) == 3 and any("solo se probó funding constante" in n for n in result.notes)


def test_a_failing_point_is_reported_without_aborting_the_analysis() -> None:
    candles = make_candles(600)
    bad = dataclasses.replace(CFG, execution=dataclasses.replace(CFG.execution, taker_fee_pct=-1.0))  # invalida -> cada punto falla o no
    results = run_stress_tests(DONCHIAN, DONCHIAN.params_model(), bad, market_for(candles), None)
    assert len(results) == 10  # todos los escenarios vuelven, con error o metricas


def test_analysis_can_be_cancelled() -> None:
    p = Progress()
    p.cancelled = True
    candles = make_candles(600)
    with pytest.raises(Cancelled):
        run_stress_tests(DONCHIAN, DONCHIAN.params_model(), CFG, market_for(candles), None, p)
    with pytest.raises(Cancelled):
        run_cost_sensitivity(DONCHIAN, DONCHIAN.params_model(), CFG, market_for(candles), None, Progress(cancelled=True))
