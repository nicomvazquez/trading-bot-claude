import numpy as np
import pandas as pd
import pytest

from app.strategies import registry
from app.strategies.base import StrategyContext
from app.strategies.examples.prev_day_breakout import PrevDayBreakoutParams, PrevDayBreakoutStrategy

START = pd.Timestamp("2026-01-01 00:00", tz="UTC")
FLAT = (100.0, 100.2, 99.8, 100.0)


def _candles(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range(START, periods=len(bars), freq="15min")
    arr = np.array(bars, dtype=float)
    return pd.DataFrame({"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2], "close": arr[:, 3], "volume": 1.0}, index=idx)


def _day1() -> list[tuple[float, float, float, float]]:
    """Día anterior con máximo 101 y mínimo 99 (niveles de la ruptura)."""
    bars = [FLAT] * 96
    bars[10] = (100.0, 101.0, 99.9, 100.0)
    bars[20] = (100.0, 100.1, 99.0, 100.0)
    return bars


def _day2_until(hour: int) -> list[tuple[float, float, float, float]]:
    return [FLAT] * (hour * 4)


def _stream(strategy, candles: pd.DataFrame, start: int = 0):
    out = []
    for i in range(start, len(candles)):
        sig = strategy.on_candle(StrategyContext(candles=candles.iloc[: i + 1], position=None, equity=1000.0))
        if sig is not None:
            out.append((i, sig))
    return out


def test_registered_on_15_minutes() -> None:
    cls = registry.get("prev_day_breakout")
    assert cls is PrevDayBreakoutStrategy
    assert cls.default_timeframe == "15"


def test_close_above_yesterdays_high_in_newyork_buys_once() -> None:
    bars = _day1() + _day2_until(13) + [(100.0, 100.4, 99.9, 100.2), (100.2, 101.8, 100.1, 101.5), (101.5, 102.0, 101.4, 101.8)]
    signals = _stream(PrevDayBreakoutStrategy(PrevDayBreakoutParams()), _candles(bars), start=96)
    assert len(signals) == 1
    idx, sig = signals[0]
    assert idx == 96 + 52 + 1 and sig.action == "buy"
    assert sig.stop_loss < 101.5 < sig.take_profit


def test_close_below_yesterdays_low_sells() -> None:
    bars = _day1() + _day2_until(13) + [(100.0, 100.2, 99.8, 100.0), (100.0, 100.1, 98.2, 98.5)]
    signals = _stream(PrevDayBreakoutStrategy(PrevDayBreakoutParams()), _candles(bars), start=96)
    assert len(signals) == 1 and signals[0][1].action == "sell"
    assert signals[0][1].take_profit < 98.5 < signals[0][1].stop_loss


def test_level_broken_earlier_in_the_day_is_not_traded() -> None:
    bars = _day1() + [(100.0, 101.8, 99.9, 101.5)] + _day2_until(13)[1:] + [(100.0, 101.9, 100.1, 101.6)]
    assert _stream(PrevDayBreakoutStrategy(PrevDayBreakoutParams()), _candles(bars), start=96) == []


def test_outside_the_session_windows_nothing_happens() -> None:
    bars = _day1() + _day2_until(3) + [(100.0, 101.8, 100.0, 101.5)]
    assert _stream(PrevDayBreakoutStrategy(PrevDayBreakoutParams()), _candles(bars), start=96) == []


def test_daily_cap_blocks_the_second_break() -> None:
    bars = _day1() + _day2_until(7) + [(100.0, 101.8, 100.0, 101.5)] + _day2_until(13)[28:] + [(100.0, 100.1, 98.2, 98.5)]
    signals = _stream(PrevDayBreakoutStrategy(PrevDayBreakoutParams(max_trades_per_day=1)), _candles(bars), start=96)
    assert len(signals) == 1 and signals[0][1].action == "buy"


def test_signals_never_depend_on_future_candles() -> None:
    rng = np.random.default_rng(12)
    closes = 100 + np.cumsum(rng.normal(0, 0.15, 60 * 96))
    bars = [(c - 0.05, c + 0.2, c - 0.2, c) for c in closes]
    base = _candles(bars)
    altered = base.copy()
    cutoff = 40 * 96
    altered.iloc[cutoff:, altered.columns.get_loc("close")] += rng.normal(0, 3, len(altered) - cutoff)
    altered["high"] = np.maximum(altered["high"], altered["close"])
    altered["low"] = np.minimum(altered["low"], altered["close"])
    original = [i for i, _ in _stream(PrevDayBreakoutStrategy(PrevDayBreakoutParams()), base, start=96) if i < cutoff]
    shifted = [i for i, _ in _stream(PrevDayBreakoutStrategy(PrevDayBreakoutParams()), altered, start=96) if i < cutoff]
    assert original == shifted


def test_same_candles_same_signals() -> None:
    rng = np.random.default_rng(13)
    closes = 100 + np.cumsum(rng.normal(0, 0.15, 30 * 96))
    candles = _candles([(c - 0.05, c + 0.2, c - 0.2, c) for c in closes])
    first = [(i, s.action, s.take_profit) for i, s in _stream(PrevDayBreakoutStrategy(PrevDayBreakoutParams()), candles, start=96)]
    second = [(i, s.action, s.take_profit) for i, s in _stream(PrevDayBreakoutStrategy(PrevDayBreakoutParams()), candles, start=96)]
    assert first == second
