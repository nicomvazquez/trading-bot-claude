import numpy as np
import pandas as pd
import pytest

from app.strategies import registry
from app.strategies.base import Position, StrategyContext
from app.strategies.examples.ema_crossover import EmaCrossoverParams, EmaCrossoverStrategy
from app.strategies.indicators import atr_padded

START = pd.Timestamp("2026-01-01 00:00", tz="UTC")
OFF = dict(trend_filter_enabled=False, trend_slope_enabled=False, adx_filter_enabled=False,
           volume_filter_enabled=False, volatility_filter_enabled=False, cooldown_after_trade_bars=0,
           max_trades_per_day=20, trend_ema_period=20)  # la EMA de 200 exige 600 velas de historia


def _candles(closes: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range(START, periods=len(closes), freq="5min")
    open_ = np.r_[closes[0], closes[:-1]]
    wick = 0.05 + (np.arange(len(closes)) % 7) * 0.007
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, closes) + wick,
                         "low": np.minimum(open_, closes) - wick, "close": closes, "volume": 1.0}, index=idx)


def _stream(strategy, candles: pd.DataFrame, start: int = 0):
    out = []
    for i in range(start, len(candles)):
        sig = strategy.on_candle(StrategyContext(candles=candles.iloc[: i + 1], position=None, equity=1000.0))
        if sig is not None:
            out.append((i, sig))
    return out


def _cross_indices(closes: np.ndarray, fast_n: int, slow_n: int, bull: bool) -> list[int]:
    s = pd.Series(closes)
    f = s.ewm(span=fast_n, adjust=False).mean().to_numpy()
    sl = s.ewm(span=slow_n, adjust=False).mean().to_numpy()
    out = []
    for k in range(2, len(closes)):
        if bull and f[k - 1] <= sl[k - 1] and f[k] > sl[k]:
            out.append(k)
        if not bull and f[k - 1] >= sl[k - 1] and f[k] < sl[k]:
            out.append(k)
    return out


def _v_series(seed: int = 1) -> np.ndarray:
    """Caida, rebote y nueva caida: genera cruces alcistas y bajistas sin depender de filtros."""
    rng = np.random.default_rng(seed)
    down = np.linspace(100, 90, 300)
    up = np.linspace(90, 110, 300)
    down2 = np.linspace(110, 95, 300)
    return np.concatenate([down, up, down2]) + rng.normal(0, 0.05, 900)


def test_registered_on_five_minutes() -> None:
    cls = registry.get("ema_crossover")
    assert cls is EmaCrossoverStrategy and cls.default_timeframe == "5"


def test_bullish_crossover_gives_a_buy_exactly_on_the_cross_bars() -> None:
    closes = _v_series()
    expected = _cross_indices(closes, 9, 21, bull=True)
    signals = _stream(EmaCrossoverStrategy(EmaCrossoverParams(**OFF)), _candles(closes), start=30)
    got = [i for i, s in signals if s.action == "buy"]
    assert expected and got == [i for i in expected if i >= 30]


def test_bearish_crossover_gives_a_sell_on_the_cross_bars() -> None:
    closes = _v_series()
    mirrored = 200 - closes
    expected = _cross_indices(mirrored, 9, 21, bull=False)
    signals = _stream(EmaCrossoverStrategy(EmaCrossoverParams(**OFF)), _candles(mirrored), start=30)
    got = [i for i, s in signals if s.action == "sell"]
    assert expected and got == [i for i in expected if i >= 30]


def test_next_candle_confirmation_does_not_enter_on_the_cross_bar() -> None:
    closes = _v_series()
    at_cross = {i for i, _ in _stream(EmaCrossoverStrategy(EmaCrossoverParams(**OFF)), _candles(closes), start=30)}
    nxt = _stream(EmaCrossoverStrategy(EmaCrossoverParams(**OFF, next_candle_confirmation=True)), _candles(closes), start=30)
    assert nxt and not any(i in at_cross for i, _ in nxt)


def test_trend_filter_blocks_longs_when_price_is_below_the_trend() -> None:
    closes = _v_series() - 60
    params = EmaCrossoverParams(**{**OFF, "trend_filter_enabled": True, "trend_ema_period": 20})
    signals = _stream(EmaCrossoverStrategy(params), _candles(closes), start=30)
    assert not any(s.action == "buy" for _, s in signals)


def test_adx_filter_only_allows_entries_with_adx_above_minimum() -> None:
    from app.strategies.indicators import adx_series
    candles = _candles(_v_series())
    params = EmaCrossoverParams(**{**OFF, "adx_filter_enabled": True, "adx_minimum": 30.0})
    signals = _stream(EmaCrossoverStrategy(params), candles, start=30)
    adx = adx_series(candles["high"], candles["low"], candles["close"], 14)
    assert all(adx.iloc[i] >= 30.0 for i, _ in signals)
    unfiltered = _stream(EmaCrossoverStrategy(EmaCrossoverParams(**OFF)), candles, start=30)
    assert len(signals) <= len(unfiltered)


def test_volume_filter_blocks_when_volume_is_insufficient() -> None:
    params = EmaCrossoverParams(**{**OFF, "volume_filter_enabled": True, "minimum_relative_volume": 10.0})
    assert _stream(EmaCrossoverStrategy(params), _candles(_v_series()), start=30) == []


def test_volatility_filter_blocks_when_volatility_is_out_of_range() -> None:
    params = EmaCrossoverParams(**{**OFF, "volatility_filter_enabled": True, "minimum_atr_ratio": 10.0})
    assert _stream(EmaCrossoverStrategy(params), _candles(_v_series()), start=30) == []


def test_stop_and_take_profit_follow_the_atr_and_rr_rules() -> None:
    candles = _candles(_v_series())
    signals = _stream(EmaCrossoverStrategy(EmaCrossoverParams(**OFF)), candles, start=30)
    i, sig = next((i, s) for i, s in signals if s.action == "buy")
    tail = candles.iloc[: i + 1].iloc[-400:]
    atr = atr_padded(tail["high"].to_numpy(float), tail["low"].to_numpy(float), tail["close"].to_numpy(float), 14)[-1]
    entry = float(candles["close"].iloc[i])
    assert sig.stop_loss == pytest.approx(entry - 1.2 * atr)
    assert sig.take_profit == pytest.approx(entry + 2.0 * (entry - sig.stop_loss))


def test_time_stop_closes_after_the_configured_bars() -> None:
    strategy = EmaCrossoverStrategy(EmaCrossoverParams(max_bars_in_trade=3))
    candles = _candles(_v_series())
    pos = Position(side="long", entry_price=100.0, qty=1.0)
    actions = []
    for i in range(10, 13):
        sig = strategy.on_candle(StrategyContext(candles=candles.iloc[: i + 1], position=pos, equity=1000.0))
        actions.append(sig.action if sig else None)
    assert actions == [None, None, "close"]


def test_daily_cap_is_respected() -> None:
    candles = _candles(_v_series())
    signals = _stream(EmaCrossoverStrategy(EmaCrossoverParams(**{**OFF, "max_trades_per_day": 1})), candles, start=30)
    per_day = {}
    for i, _ in signals:
        d = candles.index[i].date()
        per_day[d] = per_day.get(d, 0) + 1
    assert all(v <= 1 for v in per_day.values())


def test_cooldown_spaces_out_entries() -> None:
    signals = _stream(EmaCrossoverStrategy(EmaCrossoverParams(**{**OFF, "cooldown_after_trade_bars": 40})), _candles(_v_series()), start=30)
    idx = [i for i, _ in signals]
    assert all(b - a >= 40 for a, b in zip(idx, idx[1:]))


def test_pullback_mode_never_enters_on_the_cross_bar() -> None:
    closes = _v_series()
    cross = set(_cross_indices(closes, 9, 21, bull=True))
    params = EmaCrossoverParams(**{**OFF, "pullback_entry_enabled": True, "max_pullback_bars": 30, "pullback_tolerance_pct": 0.5})
    buys = [i for i, s in _stream(EmaCrossoverStrategy(params), _candles(closes), start=30) if s.action == "buy"]
    assert all(i not in cross for i in buys)


def test_signals_never_depend_on_future_candles() -> None:
    rng = np.random.default_rng(21)
    base_closes = 100 + np.cumsum(rng.normal(0, 0.1, 2000))
    base = _candles(base_closes)
    cutoff = 1400
    altered_closes = base_closes.copy()
    altered_closes[cutoff:] += rng.normal(0, 2, len(altered_closes) - cutoff)
    altered = _candles(altered_closes)
    original = [i for i, _ in _stream(EmaCrossoverStrategy(EmaCrossoverParams()), base, start=300) if i < cutoff]
    shifted = [i for i, _ in _stream(EmaCrossoverStrategy(EmaCrossoverParams()), altered, start=300) if i < cutoff]
    assert original == shifted


def test_context_trend_ignores_the_forming_context_bar() -> None:
    candles = _candles(_v_series())
    prefix = candles.iloc[:898]  # la última vela abre una barra de 15 min: esa barra está en formación
    t1 = EmaCrossoverStrategy(EmaCrossoverParams())._trend_state(prefix, pd.Timedelta(minutes=5), 100.0)
    changed = prefix.copy()
    changed.iloc[-1, changed.columns.get_loc("close")] += 50.0
    t2 = EmaCrossoverStrategy(EmaCrossoverParams())._trend_state(changed, pd.Timedelta(minutes=5), 100.0)
    assert t1 == t2


def test_same_candles_same_signals() -> None:
    candles = _candles(_v_series(seed=4))
    first = [(i, s.action, s.take_profit) for i, s in _stream(EmaCrossoverStrategy(EmaCrossoverParams()), candles, start=30)]
    second = [(i, s.action, s.take_profit) for i, s in _stream(EmaCrossoverStrategy(EmaCrossoverParams()), candles, start=30)]
    assert first == second
