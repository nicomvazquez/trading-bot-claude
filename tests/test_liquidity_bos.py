import numpy as np
import pandas as pd

from app.strategies import registry
from app.strategies.base import StrategyContext
from app.strategies.price_action.liquidity_bos import LiquidityBosParams, LiquidityBosStrategy

START = pd.Timestamp("2026-01-01 00:00", tz="UTC")


def _candles(closes: np.ndarray, freq: str = "5min") -> pd.DataFrame:
    idx = pd.date_range(START, periods=len(closes), freq=freq)
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


def _random_walk(n: int, seed: int, drift: float = 0.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 100 + np.cumsum(rng.normal(drift, 0.12, n))


def test_registered_on_five_minutes() -> None:
    cls = registry.get("liquidity_bos_retest")
    assert cls.default_timeframe == "5"


def test_every_signal_has_a_stop_below_and_target_above_for_longs() -> None:
    candles = _candles(_random_walk(4000, seed=1))
    signals = _stream(LiquidityBosStrategy(LiquidityBosParams()), candles, start=300)
    assert signals, "con datos aleatorios debía haber al menos una señal"
    for _, sig in signals:
        if sig.action == "buy":
            assert sig.stop_loss < sig.take_profit
        else:
            assert sig.take_profit < sig.stop_loss


def test_minimum_risk_reward_is_enforced() -> None:
    candles = _candles(_random_walk(4000, seed=1))
    signals = _stream(LiquidityBosStrategy(LiquidityBosParams(minimum_risk_reward=10.0)), candles, start=300)
    for i, sig in signals:
        entry = float(candles["close"].iloc[i])
        risk = abs(entry - sig.stop_loss)
        assert abs(sig.take_profit - entry) >= 10.0 * risk - 1e-9


def test_daily_cap_is_respected() -> None:
    candles = _candles(_random_walk(4000, seed=2))
    signals = _stream(LiquidityBosStrategy(LiquidityBosParams(max_trades_per_day=1, cooldown_after_trade_bars=0)), candles, start=300)
    per_day = {}
    for i, _ in signals:
        d = candles.index[i].date()
        per_day[d] = per_day.get(d, 0) + 1
    assert all(v <= 1 for v in per_day.values())


def test_session_filter_blocks_outside_hours() -> None:
    candles = _candles(_random_walk(4000, seed=3))
    signals = _stream(LiquidityBosStrategy(LiquidityBosParams(session_filter_enabled=True, session_start_hour=8, session_end_hour=9)), candles, start=300)
    assert all(8 <= candles.index[i].hour < 9 for i, _ in signals)


def test_trend_only_with_filter_never_trades_against_an_uptrend() -> None:
    closes = np.linspace(100, 200, 4000) + np.cumsum(np.random.default_rng(4).normal(0, 0.05, 4000)) * 0.1
    candles = _candles(closes)
    signals = _stream(LiquidityBosStrategy(LiquidityBosParams(regime_filter_enabled=True, allow_trend_trades=True, allow_reversals=False)), candles, start=300)
    assert not any(sig.action == "sell" for _, sig in signals)


def test_signals_never_depend_on_future_candles() -> None:
    base_closes = _random_walk(3000, seed=5)
    base = _candles(base_closes)
    altered_closes = base_closes.copy()
    cutoff = 2000
    altered_closes[cutoff:] += np.random.default_rng(6).normal(0, 3, len(altered_closes) - cutoff)
    altered = _candles(altered_closes)
    original = [i for i, _ in _stream(LiquidityBosStrategy(LiquidityBosParams()), base, start=300) if i < cutoff]
    shifted = [i for i, _ in _stream(LiquidityBosStrategy(LiquidityBosParams()), altered, start=300) if i < cutoff]
    assert original == shifted


def test_same_candles_same_signals() -> None:
    candles = _candles(_random_walk(3000, seed=7))
    first = [(i, s.action, s.take_profit) for i, s in _stream(LiquidityBosStrategy(LiquidityBosParams()), candles, start=300)]
    second = [(i, s.action, s.take_profit) for i, s in _stream(LiquidityBosStrategy(LiquidityBosParams()), candles, start=300)]
    assert first == second
