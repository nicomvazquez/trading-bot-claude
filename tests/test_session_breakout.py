import numpy as np
import pandas as pd
import pytest

from app.strategies import registry
from app.strategies.base import StrategyContext
from app.strategies.examples.session_breakout import SessionBreakoutParams, SessionBreakoutStrategy

START = pd.Timestamp("2026-01-01 00:00", tz="UTC")
NY_ONLY = dict(use_asia=False, use_london=False)


def _candles(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range(START, periods=len(bars), freq="15min")
    rows = {"open": [], "high": [], "low": [], "close": []}
    for o, h, l, c in bars:
        rows["open"].append(o); rows["high"].append(h); rows["low"].append(l); rows["close"].append(c)
    return pd.DataFrame(rows, index=idx).assign(volume=1.0)


def _flat_until_ny() -> list[tuple[float, float, float, float]]:
    # 00:00 a 12:45 UTC: 53 velas planas (ATR estable, sin rangos de sesión)
    return [(100.0, 100.2, 99.8, 100.0)] * 53


def _stream(strategy, candles: pd.DataFrame, start: int = 0):
    out = []
    for i in range(start, len(candles)):
        sig = strategy.on_candle(StrategyContext(candles=candles.iloc[: i + 1], position=None, equity=1000.0))
        if sig is not None:
            out.append((i, sig))
    return out


def test_registered_on_15_minutes() -> None:
    cls = registry.get("session_breakout")
    assert cls is SessionBreakoutStrategy
    assert cls.default_timeframe == "15"


def test_breakout_above_the_newyork_range_buys_once() -> None:
    bars = _flat_until_ny() + [
        (100.0, 100.5, 99.8, 100.2),  # 13:00 rango
        (100.2, 100.5, 99.9, 100.1),  # 13:15 rango
        (100.1, 101.2, 100.0, 101.0),  # 13:30 cierre fuera del rango: ruptura alcista
        (101.0, 101.4, 100.9, 101.2),  # 13:45 ya operado: no debe haber otra señal
    ]
    signals = _stream(SessionBreakoutStrategy(SessionBreakoutParams(**NY_ONLY)), _candles(bars))
    assert len(signals) == 1
    idx, sig = signals[0]
    assert idx == 55 and sig.action == "buy"
    assert sig.stop_loss == pytest.approx(99.8 * (1 - 0.05 / 100))
    assert sig.take_profit == pytest.approx(101.0 + 2.0 * (101.0 - sig.stop_loss))


def test_breakout_below_the_range_sells() -> None:
    bars = _flat_until_ny() + [
        (100.0, 100.5, 99.8, 100.2),
        (100.2, 100.5, 99.9, 100.1),
        (99.9, 100.0, 98.8, 99.0),
    ]
    sig = _stream(SessionBreakoutStrategy(SessionBreakoutParams(**NY_ONLY)), _candles(bars))[0][1]
    assert sig.action == "sell"
    assert sig.take_profit < 99.0 < sig.stop_loss


def test_a_range_that_was_already_broken_is_not_traded() -> None:
    bars = _flat_until_ny() + [
        (100.0, 100.5, 99.8, 100.2),
        (100.2, 100.5, 99.9, 100.1),
        (100.1, 101.2, 100.0, 101.0),  # ruptura: operación
        (101.0, 101.5, 100.8, 101.3),
    ]
    assert len(_stream(SessionBreakoutStrategy(SessionBreakoutParams(**NY_ONLY)), _candles(bars))) == 1
    late = _flat_until_ny() + [
        (100.0, 100.5, 99.8, 100.2),
        (100.2, 100.5, 99.9, 100.1),
        (100.1, 100.4, 99.9, 100.0),  # sin ruptura
        (100.0, 101.2, 99.9, 101.0),  # la ruptura llega fuera de la ventana de entrada
    ] + [(100.0, 100.2, 99.9, 100.0)] * 9
    late[-1] = (100.0, 101.2, 99.9, 101.0)
    assert _stream(SessionBreakoutStrategy(SessionBreakoutParams(**NY_ONLY, entry_window_bars=2)), _candles(late)) == []


def test_range_too_wide_for_the_atr_is_skipped() -> None:
    bars = _flat_until_ny() + [
        (100.0, 103.0, 97.0, 100.2),
        (100.2, 103.0, 97.0, 100.1),
        (100.1, 104.0, 100.0, 103.5),
    ]
    assert _stream(SessionBreakoutStrategy(SessionBreakoutParams(**NY_ONLY, range_max_atr=2.0)), _candles(bars)) == []


def test_disabled_session_is_not_traded() -> None:
    bars = _flat_until_ny() + [
        (100.0, 100.5, 99.8, 100.2),
        (100.2, 100.5, 99.9, 100.1),
        (100.1, 101.2, 100.0, 101.0),
    ]
    assert _stream(SessionBreakoutStrategy(SessionBreakoutParams(use_newyork=False)), _candles(bars)) == []


def test_signals_never_depend_on_future_candles() -> None:
    rng = np.random.default_rng(21)
    closes = 100 + np.cumsum(rng.normal(0, 0.2, 800))
    bars = [(c - 0.1, c + 0.3, c - 0.3, c) for c in closes]
    base = _candles(bars)
    altered = base.copy()
    cutoff = 500
    altered.iloc[cutoff:, altered.columns.get_loc("close")] += rng.normal(0, 3, len(altered) - cutoff)
    altered["high"] = np.maximum(altered["high"], altered["close"])
    altered["low"] = np.minimum(altered["low"], altered["close"])
    original = [i for i, _ in _stream(SessionBreakoutStrategy(SessionBreakoutParams()), base, start=60) if i < cutoff]
    shifted = [i for i, _ in _stream(SessionBreakoutStrategy(SessionBreakoutParams()), altered, start=60) if i < cutoff]
    assert original == shifted


def test_same_candles_same_signals() -> None:
    rng = np.random.default_rng(2)
    closes = 100 + np.cumsum(rng.normal(0, 0.2, 800))
    candles = _candles([(c - 0.1, c + 0.3, c - 0.3, c) for c in closes])
    first = [(i, s.action, s.take_profit) for i, s in _stream(SessionBreakoutStrategy(SessionBreakoutParams()), candles)]
    second = [(i, s.action, s.take_profit) for i, s in _stream(SessionBreakoutStrategy(SessionBreakoutParams()), candles)]
    assert first == second
