import pandas as pd

from app.strategies import registry
from app.strategies.base import StrategyContext
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
