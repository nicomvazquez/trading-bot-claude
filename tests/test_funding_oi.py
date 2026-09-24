import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.backtest.config import BacktestConfig
from app.backtest.engine import Backtester
from app.backtest.extras import attach_series
from app.strategies.base import Position, StrategyContext
from app.strategies.examples.funding_oi import FundingOiParams, FundingOiStrategy

T0 = pd.Timestamp("2026-01-01", tz="UTC")


def make_candles(n: int, closes=None, funding=None, oi=None) -> pd.DataFrame:
    idx = pd.date_range(T0, periods=n, freq="1h")
    closes = np.full(n, 100.0) if closes is None else np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "open": closes, "high": closes * 1.001, "low": closes * 0.999, "close": closes, "volume": 1.0,
        "funding_rate": np.linspace(-0.0001, 0.0001, n) if funding is None else funding,
        "open_interest": np.linspace(1000, 1400, n) if oi is None else oi,
    }, index=idx)


def params(**kw) -> FundingOiParams:
    return FundingOiParams(lookback=50, oi_bars=6, trigger_bars=4, **kw)


def crowded_long_setup(n: int = 80) -> pd.DataFrame:
    """Funding en su maximo, OI subiendo y una ruptura a la baja en la ultima vela."""
    closes = np.full(n, 100.0)
    closes[-1] = 98.0  # cierra bajo el minimo de las ultimas velas
    return make_candles(n, closes=closes)  # funding sube monotono -> percentil 100 al final


# ------------------------------------------------------------------ union sin look-ahead

def test_attach_series_only_uses_data_known_before_the_candle_opens() -> None:
    candles = make_candles(6)
    series = pd.Series([10.0, 20.0], index=pd.DatetimeIndex([T0 + pd.Timedelta(hours=2), T0 + pd.Timedelta(hours=4)]))
    out = attach_series(candles, series, "x", dt.timedelta(hours=9))
    assert out["x"].iloc[:2].isna().all()            # antes del primer dato: nada
    assert out["x"].iloc[2] == 10.0                  # dato estampado 02:00 disponible desde la vela de las 02:00
    assert out["x"].iloc[3] == 10.0
    assert out["x"].iloc[4] == 20.0 and out["x"].iloc[5] == 20.0


def test_attach_series_does_not_fill_long_gaps() -> None:
    candles = make_candles(30)
    series = pd.Series([5.0], index=pd.DatetimeIndex([T0]))
    out = attach_series(candles, series, "x", dt.timedelta(hours=3))
    assert out["x"].iloc[3] == 5.0
    assert out["x"].iloc[4:].isna().all()            # mas viejo que max_age: no se arrastra


def test_attach_series_with_no_data_gives_nan_column() -> None:
    out = attach_series(make_candles(3), pd.Series(dtype=float), "x", dt.timedelta(hours=1))
    assert out["x"].isna().all()


# ------------------------------------------------------------------ senales

def test_short_when_longs_are_crowded_and_price_breaks_down() -> None:
    candles = crowded_long_setup()
    s = FundingOiStrategy(params())
    sig = s.on_candle(StrategyContext(candles=candles, position=None, equity=1000))
    assert sig is not None and sig.action == "sell"
    price = 98.0
    assert sig.stop_loss == pytest.approx(price * 1.025)
    assert sig.take_profit == pytest.approx(price - price * 0.025 * 2.0)


def test_long_when_shorts_are_crowded_and_price_breaks_up() -> None:
    n = 80
    closes = np.full(n, 100.0)
    closes[-1] = 102.0
    candles = make_candles(n, closes=closes, funding=np.linspace(0.0001, -0.0001, n))  # funding en su minimo
    sig = FundingOiStrategy(params()).on_candle(StrategyContext(candles=candles, position=None, equity=1000))
    assert sig is not None and sig.action == "buy" and sig.stop_loss < 102 < sig.take_profit


def test_no_entry_without_the_breakdown_trigger() -> None:
    candles = make_candles(80)  # funding extremo pero el precio no rompe
    assert FundingOiStrategy(params()).on_candle(StrategyContext(candles=candles, position=None, equity=1000)) is None


def test_no_entry_when_open_interest_is_falling() -> None:
    candles = crowded_long_setup()
    candles["open_interest"] = np.linspace(1100, 1000, len(candles))
    assert FundingOiStrategy(params()).on_candle(StrategyContext(candles=candles, position=None, equity=1000)) is None


def test_direction_switches_are_respected() -> None:
    candles = crowded_long_setup()
    s = FundingOiStrategy(params(allow_short=False))
    assert s.on_candle(StrategyContext(candles=candles, position=None, equity=1000)) is None


def test_missing_data_means_no_signal() -> None:
    candles = crowded_long_setup()
    candles.iloc[-1, candles.columns.get_loc("funding_rate")] = np.nan
    assert FundingOiStrategy(params()).on_candle(StrategyContext(candles=candles, position=None, equity=1000)) is None
    assert FundingOiStrategy(params()).on_candle(StrategyContext(candles=candles.drop(columns=["open_interest"]), position=None, equity=1000)) is None


def test_closes_the_short_when_funding_normalizes() -> None:
    n = 80
    funding = np.concatenate([np.linspace(0.0, 0.0002, n - 1), [0.0001]])  # baja al percentil ~50
    candles = make_candles(n, funding=funding)
    pos = Position(side="short", entry_price=100.0, qty=1.0)
    sig = FundingOiStrategy(params()).on_candle(StrategyContext(candles=candles, position=pos, equity=1000))
    assert sig is not None and sig.action == "close"
    # con el funding todavia extremo mantiene
    still = make_candles(n)
    assert FundingOiStrategy(params()).on_candle(StrategyContext(candles=still, position=pos, equity=1000)) is None


# ------------------------------------------------------------------ sin look-ahead y en el motor

def test_changing_data_stamped_after_a_candle_opens_never_changes_that_candle() -> None:
    """La union de datos es la fuente real de look-ahead: el valor de la columna en la vela i no puede
    depender de ningun dato estampado despues de la apertura de i."""
    rng = np.random.default_rng(3)
    candles = make_candles(200)
    stamps = pd.DatetimeIndex([T0 + pd.Timedelta(hours=h, minutes=17) for h in range(0, 200, 3)])
    original = pd.Series(rng.normal(0, 1, len(stamps)), index=stamps)
    base = attach_series(candles, original, "x", dt.timedelta(hours=9))["x"]
    for i in (20, 77, 150):
        tampered = original.copy()
        tampered[tampered.index > candles.index[i]] += 1000.0  # todo lo estampado despues de la apertura de la vela i
        changed = attach_series(candles, tampered, "x", dt.timedelta(hours=9))["x"]
        pd.testing.assert_series_equal(base.iloc[: i + 1], changed.iloc[: i + 1])
        assert (base.iloc[i + 4:].dropna() != changed.iloc[i + 4:].dropna()).any()  # el test no es vacuo


def test_runs_inside_the_backtester_with_short_trades() -> None:
    rng = np.random.default_rng(11)
    n = 900
    closes = 100 + np.cumsum(rng.normal(0, 0.5, n))
    funding = np.cumsum(rng.normal(0, 0.00003, n))
    oi = 1000 + np.cumsum(rng.normal(0.5, 4, n))
    candles = make_candles(n, closes=closes, funding=funding, oi=oi)
    cfg = BacktestConfig(timeframe="60")
    result = Backtester(execution=cfg.execution, risk=cfg.risk).run(
        FundingOiStrategy(params(oi_min_change_pct=0.0, funding_high_pct=80, funding_low_pct=20)), candles, 1000.0
    )
    assert len(result.trades) > 0
    assert {t.side for t in result.trades} <= {"long", "short"}
    assert result.equity_curve.iloc[-1] == pytest.approx(1000.0 + sum(t.pnl for t in result.trades))
