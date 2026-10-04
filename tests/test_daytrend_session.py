import collections

import numpy as np
import pandas as pd

from app.strategies import registry
from app.strategies.base import StrategyContext
from app.strategies.examples.daytrend_session import DaytrendSessionParams, DaytrendSessionStrategy

START = pd.Timestamp("2026-01-01 00:00", tz="UTC")


def _candles(closes: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range(START, periods=len(closes), freq="15min")
    open_ = np.r_[closes[0], closes[:-1]]
    wick = 0.05 + (np.arange(len(closes)) % 7) * 0.007  # mechas distintas: sin mínimos empatados
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, closes) + wick,
                         "low": np.minimum(open_, closes) - wick, "close": closes, "volume": 1.0}, index=idx)


def _stream(strategy, candles: pd.DataFrame, start: int = 0):
    out = []
    for i in range(start, len(candles)):
        sig = strategy.on_candle(StrategyContext(candles=candles.iloc[: i + 1], position=None, equity=1000.0))
        if sig is not None:
            out.append((i, sig))
    return out


def _uptrend(days: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = days * 96
    drift = np.linspace(100, 200, n)
    wave = 2.0 * np.sin(np.arange(n) * 2 * np.pi / 40)
    return drift + wave + rng.normal(0, 0.1, n)


def test_registered_on_15_minutes() -> None:
    cls = registry.get("daytrend_session")
    assert cls is DaytrendSessionStrategy
    assert cls.default_timeframe == "15"


def test_uptrend_gives_longs_and_never_more_than_two_per_day() -> None:
    candles = _candles(_uptrend(30, seed=1))
    signals = _stream(DaytrendSessionStrategy(DaytrendSessionParams()), candles, start=20 * 96)
    assert signals, "en una tendencia alcista debía haber al menos una operación"
    assert all(s.action == "buy" for _, s in signals)
    per_day = collections.Counter(candles.index[i].date() for i, _ in signals)
    assert max(per_day.values()) <= 2


def test_downtrend_gives_no_longs() -> None:
    closes = 200 - (np.linspace(0, 100, 30 * 96))
    closes = closes + np.cumsum(np.random.default_rng(3).normal(0, 0.03, len(closes)))
    signals = _stream(DaytrendSessionStrategy(DaytrendSessionParams()), _candles(closes), start=20 * 96)
    assert not any(s.action == "buy" for _, s in signals)


def test_daily_cap_is_respected() -> None:
    candles = _candles(_uptrend(30, seed=2))
    signals = _stream(DaytrendSessionStrategy(DaytrendSessionParams(max_trades_per_day=1)), candles, start=20 * 96)
    per_day = collections.Counter(candles.index[i].date() for i, _ in signals)
    assert all(v <= 1 for v in per_day.values())


def test_signals_never_depend_on_future_candles() -> None:
    candles = _candles(_uptrend(24, seed=4))
    altered = candles.copy()
    cutoff = 18 * 96
    rng = np.random.default_rng(9)
    altered.iloc[cutoff:, altered.columns.get_loc("close")] += rng.normal(0, 4, len(altered) - cutoff)
    altered["high"] = np.maximum(altered["high"], altered["close"])
    altered["low"] = np.minimum(altered["low"], altered["close"])
    original = [i for i, _ in _stream(DaytrendSessionStrategy(DaytrendSessionParams()), candles, start=20 * 96) if i < cutoff]
    shifted = [i for i, _ in _stream(DaytrendSessionStrategy(DaytrendSessionParams()), altered, start=20 * 96) if i < cutoff]
    assert original == shifted


def test_same_candles_same_signals() -> None:
    candles = _candles(_uptrend(24, seed=5))
    first = [(i, s.action, s.take_profit) for i, s in _stream(DaytrendSessionStrategy(DaytrendSessionParams()), candles, start=20 * 96)]
    second = [(i, s.action, s.take_profit) for i, s in _stream(DaytrendSessionStrategy(DaytrendSessionParams()), candles, start=20 * 96)]
    assert first == second
