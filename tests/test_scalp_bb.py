import numpy as np
import pandas as pd
import pytest

from app.strategies import registry
from app.strategies.base import StrategyContext
from app.strategies.examples.scalp_bb_reversion import ScalpBbParams, ScalpBbReversionStrategy

START = pd.Timestamp("2026-01-01 00:00", tz="UTC")


def _candles(closes: list[float]) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    idx = pd.date_range(START, periods=len(close), freq="5min")
    return pd.DataFrame({"open": np.r_[close[0], close[:-1]], "high": close + 0.05, "low": close - 0.05,
                         "close": close, "volume": 1.0}, index=idx)


def _stream(strategy, candles: pd.DataFrame, start: int = 0):
    out = []
    for i in range(start, len(candles)):
        sig = strategy.on_candle(StrategyContext(candles=candles.iloc[: i + 1], position=None, equity=1000.0))
        if sig is not None:
            out.append((i, sig))
    return out


def _base() -> list[float]:
    return [100 + 0.1 * np.sin(k / 3) for k in range(100)]


def test_registered_with_scalping_defaults() -> None:
    cls = registry.get("scalp_bb_reversion")
    assert cls is ScalpBbReversionStrategy
    assert cls.default_timeframe == "5"


def test_drop_outside_lower_band_with_oversold_rsi_buys_toward_the_mean() -> None:
    closes = _base() + [99.6, 99.1, 98.6, 98.1, 97.6, 97.1]
    signals = _stream(ScalpBbReversionStrategy(ScalpBbParams()), _candles(closes))
    assert signals, "debia haber una señal de compra"
    idx, sig = signals[-1]
    assert idx == len(closes) - 1
    assert sig.action == "buy"
    assert sig.take_profit > closes[-1]
    assert sig.stop_loss < closes[-1]


def test_mirrored_spike_gives_a_short() -> None:
    closes = _base() + [100.4, 100.9, 101.4, 101.9, 102.4, 102.9]
    signals = _stream(ScalpBbReversionStrategy(ScalpBbParams()), _candles(closes))
    assert signals
    sig = signals[-1][1]
    assert sig.action == "sell"
    assert sig.take_profit < closes[-1]
    assert sig.stop_loss > closes[-1]


def test_small_distance_to_the_mean_is_not_traded() -> None:
    closes = _base() + [99.6, 99.1, 98.6, 98.1, 97.6, 97.1]
    assert _stream(ScalpBbReversionStrategy(ScalpBbParams(min_target_pct=5.0)), _candles(closes)) == []


def test_disabled_side_is_not_traded() -> None:
    closes = _base() + [99.6, 99.1, 98.6, 98.1, 97.6, 97.1]
    assert _stream(ScalpBbReversionStrategy(ScalpBbParams(allow_long=False)), _candles(closes)) == []


def test_signals_never_depend_on_future_candles() -> None:
    rng = np.random.default_rng(5)
    closes = 100 + np.cumsum(rng.normal(0, 0.1, 1200))
    base = _candles(list(closes))
    altered = base.copy()
    cutoff = 800
    altered.iloc[cutoff:, altered.columns.get_loc("close")] += rng.normal(0, 3, len(altered) - cutoff)
    altered["high"] = np.maximum(altered["high"], altered["close"])
    altered["low"] = np.minimum(altered["low"], altered["close"])
    original = [i for i, _ in _stream(ScalpBbReversionStrategy(ScalpBbParams()), base, start=100) if i < cutoff]
    shifted = [i for i, _ in _stream(ScalpBbReversionStrategy(ScalpBbParams()), altered, start=100) if i < cutoff]
    assert original == shifted


def test_same_candles_same_signals() -> None:
    rng = np.random.default_rng(8)
    candles = _candles(list(100 + np.cumsum(rng.normal(0, 0.1, 800))))
    first = [(i, s.action, s.take_profit) for i, s in _stream(ScalpBbReversionStrategy(ScalpBbParams()), candles)]
    second = [(i, s.action, s.take_profit) for i, s in _stream(ScalpBbReversionStrategy(ScalpBbParams()), candles)]
    assert first == second


def test_target_closer_than_the_stop_is_not_traded() -> None:
    closes = _base() + [99.6, 99.1, 98.6, 98.1, 97.6, 97.1]
    assert _stream(ScalpBbReversionStrategy(ScalpBbParams(stop_atr_mult=10.0)), _candles(closes)) == []


def test_trend_filter_blocks_longs_in_a_strong_downtrend() -> None:
    rng = np.random.default_rng(4)
    closes = list(np.linspace(150, 100, 700) + rng.normal(0, 0.05, 700)) + [99.6, 99.1, 98.6, 98.1, 97.6, 97.1]
    candles = _candles(closes)
    unfiltered = _stream(ScalpBbReversionStrategy(ScalpBbParams(trend_minutes=60, trend_ema_period=20)), candles, start=100)
    filtered = _stream(ScalpBbReversionStrategy(ScalpBbParams(use_trend_filter=True)), candles, start=100)
    assert any(s.action == "buy" for _, s in unfiltered)
    assert not any(s.action == "buy" for _, s in filtered)
