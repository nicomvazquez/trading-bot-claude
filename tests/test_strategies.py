import pandas as pd

from app.strategies import registry
from app.strategies.base import Position, StrategyContext
from app.strategies.examples.rsi_reversion import RsiReversionParams, RsiReversionStrategy
from app.strategies.examples.sma_cross import SmaCrossParams, SmaCrossStrategy


def test_sma_cross_is_registered() -> None:
    assert registry.get("sma_cross") is SmaCrossStrategy


def test_sma_cross_buy_signal_on_crossover() -> None:
    strategy = SmaCrossStrategy(SmaCrossParams(fast_period=2, slow_period=5))

    closes = [10, 10, 10, 10, 10, 9, 13]
    candles = pd.DataFrame({"close": closes})
    ctx = StrategyContext(candles=candles, position=None, equity=1000.0)

    signal = strategy.on_candle(ctx)

    assert signal is not None
    assert signal.action == "buy"


def test_rsi_reversion_is_registered() -> None:
    assert registry.get("rsi_reversion") is RsiReversionStrategy


def test_rsi_reversion_buy_signal_when_oversold() -> None:
    strategy = RsiReversionStrategy(RsiReversionParams(rsi_period=14))

    closes = list(range(100, 80, -1))  # caida sostenida -> RSI cerca de 0
    candles = pd.DataFrame({"close": closes})
    ctx = StrategyContext(candles=candles, position=None, equity=1000.0)

    signal = strategy.on_candle(ctx)

    assert signal is not None
    assert signal.action == "buy"


def test_windowed_indicators_match_full_history_calculations() -> None:
    """Las estrategias miran solo las ultimas N velas por eficiencia: el resultado tiene que ser
    identico al de calcular sobre todo el historial."""
    import numpy as np

    from app.strategies.examples.rsi_reversion import _rsi

    rng = np.random.default_rng(1)
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))))
    period = 14
    full = _rsi(closes, period)
    for i in range(period + 5, len(closes)):
        windowed = _rsi(closes.iloc[: i + 1].iloc[-(period + 5):], period).iloc[-1]
        assert windowed == full.iloc[i] or abs(windowed - full.iloc[i]) < 1e-9

    fast, slow = 10, 50
    for i in range(slow + 5, len(closes)):
        head = closes.iloc[: i + 1]
        w = head.iloc[-(slow + 5):]
        assert abs(w.rolling(slow).mean().iloc[-1] - head.rolling(slow).mean().iloc[-1]) < 1e-9
        assert abs(w.rolling(fast).mean().iloc[-2] - head.rolling(fast).mean().iloc[-2]) < 1e-9


def _reference_rsi_last(closes: pd.Series, period: int) -> float:
    from app.strategies.examples.rsi_reversion import _rsi

    return float(_rsi(closes, period).iloc[-1])


def test_numpy_rsi_matches_the_pandas_reference_including_degenerate_cases() -> None:
    import math

    import numpy as np

    from app.strategies.examples.rsi_reversion import _rsi_last

    rng = np.random.default_rng(2)
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
    for period in (2, 5, 14, 30):
        for i in range(period + 5, len(closes), 7):
            head = closes.iloc[: i + 1]
            ref, fast = _reference_rsi_last(head, period), _rsi_last(head.to_numpy()[-(period + 5):], period)
            assert abs(ref - fast) < 1e-9

    up = pd.Series(np.arange(1.0, 40.0))       # solo sube: RSI 100
    down = pd.Series(np.arange(40.0, 1.0, -1))  # solo baja: RSI 0
    flat = pd.Series([50.0] * 40)                # plano: indefinido (NaN), sin senal
    assert _rsi_last(up.to_numpy(), 14) == _reference_rsi_last(up, 14) == 100.0
    assert _rsi_last(down.to_numpy(), 14) == _reference_rsi_last(down, 14) == 0.0
    assert math.isnan(_rsi_last(flat.to_numpy(), 14)) and math.isnan(_reference_rsi_last(flat, 14))


def test_numpy_sma_cross_signals_match_the_pandas_reference_over_a_whole_series() -> None:
    import numpy as np

    from app.strategies.examples.sma_cross import SmaCrossParams, SmaCrossStrategy

    rng = np.random.default_rng(4)
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 500))))
    params = SmaCrossParams(fast_period=8, slow_period=30)

    def reference(head: pd.Series):
        fast, slow = head.rolling(params.fast_period).mean(), head.rolling(params.slow_period).mean()
        prev, curr = fast.iloc[-2] - slow.iloc[-2], fast.iloc[-1] - slow.iloc[-1]
        return "buy" if (prev <= 0 and curr > 0) else ("down" if (prev >= 0 and curr < 0) else None)

    strategy = SmaCrossStrategy(params)
    checked = 0
    for i in range(params.slow_period + 2, len(closes)):
        head = closes.iloc[: i + 1]
        ref = reference(head)
        long_pos = Position(side="long", entry_price=1.0, qty=1.0)
        buy = strategy.on_candle(StrategyContext(candles=pd.DataFrame({"close": head}), position=None, equity=1000.0))
        close = strategy.on_candle(StrategyContext(candles=pd.DataFrame({"close": head}), position=long_pos, equity=1000.0))
        assert (buy is not None and buy.action == "buy") == (ref == "buy")
        assert (close is not None and close.action == "close") == (ref == "down")
        checked += ref is not None
    assert checked >= 5  # el test ejercita cruces reales
