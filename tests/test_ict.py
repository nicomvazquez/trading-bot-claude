import datetime as dt

import pandas as pd

from app.strategies import registry
from app.strategies.base import Position, StrategyContext
from app.strategies.ict.sweep_fvg import IctSweepFvgParams, IctSweepFvgStrategy


def _params(**overrides) -> IctSweepFvgParams:
    base = dict(
        swing_n=2, liquidity_lookback=40, max_bars_after_sweep=12, displacement_atr_mult=1.0,
        min_fvg_pct=0.03, sl_buffer_pct=0.05, rr_ratio=2.0,
        use_htf_bias=False, use_killzones=False,
    )
    base.update(overrides)
    return IctSweepFvgParams(**base)


def _candles(bars: list[tuple[float, float, float, float]], end: dt.datetime) -> pd.DataFrame:
    n = len(bars)
    index = pd.DatetimeIndex([end - dt.timedelta(minutes=15 * (n - 1 - i)) for i in range(n)], name="timestamp")
    return pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=index).assign(volume=1.0)


FLAT = (100.0, 101.0, 99.0, 100.0)

# 20 velas planas, un swing low en 95, barrida (mecha a 94, cierre 99.5), vela de
# desplazamiento alcista, FVG alcista (zona 100.5-102), y retroceso que lo toca.
BULLISH_BARS = (
    [FLAT] * 20
    + [FLAT] * 2
    + [(100.0, 101.0, 95.0, 99.0)]  # swing low (95), confirmado por 2 velas planas siguientes
    + [FLAT] * 3
    + [(100.0, 100.5, 94.0, 99.5)]  # barrida: mecha bajo 95, cierre sobre 95
    + [(99.5, 104.0, 99.5, 103.5)]  # desplazamiento alcista
    + [(103.5, 106.0, 102.0, 105.0)]  # deja FVG: minimo 102 > maximo de la barrida 100.5
    + [(105.0, 106.0, 104.0, 105.5)]  # sin tocar el FVG
    + [(105.5, 106.0, 101.5, 102.5)]  # primer retroceso al FVG
)

# Espejo vertical de la secuencia alcista (precio' = 200 - precio, maximos y minimos invertidos).
BEARISH_BARS = [(200 - o, 200 - l, 200 - h, 200 - c) for (o, h, l, c) in BULLISH_BARS]

NEUTRAL_END = dt.datetime(2024, 1, 10, 20, 0, tzinfo=dt.timezone.utc)  # 15:00 hora NY: fuera de kill zones
KILLZONE_END = dt.datetime(2024, 1, 10, 12, 30, tzinfo=dt.timezone.utc)  # 07:30 hora NY: kill zone de Nueva York


def _ctx(bars, end, position=None) -> StrategyContext:
    return StrategyContext(candles=_candles(bars, end), position=position, equity=1000.0)


def test_ict_strategy_is_registered() -> None:
    assert registry.get("ict_sweep_fvg") is IctSweepFvgStrategy


def test_bullish_sweep_and_fvg_generates_buy_with_levels() -> None:
    signal = IctSweepFvgStrategy(_params()).on_candle(_ctx(BULLISH_BARS, NEUTRAL_END))

    assert signal is not None
    assert signal.action == "buy"
    assert abs(signal.stop_loss - 94.0 * (1 - 0.0005)) < 1e-6  # bajo la mecha de la barrida
    entry = 102.5
    assert abs(signal.take_profit - (entry + 2.0 * (entry - signal.stop_loss))) < 1e-6


def test_bearish_sweep_and_fvg_generates_sell() -> None:
    signal = IctSweepFvgStrategy(_params()).on_candle(_ctx(BEARISH_BARS, NEUTRAL_END))

    assert signal is not None
    assert signal.action == "sell"
    assert signal.stop_loss > 106.0  # sobre la mecha de la barrida (mecha en 106)
    assert signal.take_profit < 97.5


def test_same_sweep_is_not_traded_twice() -> None:
    strategy = IctSweepFvgStrategy(_params())
    ctx = _ctx(BULLISH_BARS, NEUTRAL_END)

    assert strategy.on_candle(ctx) is not None
    assert strategy.on_candle(ctx) is None


def test_no_signal_while_in_position() -> None:
    position = Position(side="long", entry_price=100.0, qty=1.0)

    signal = IctSweepFvgStrategy(_params()).on_candle(_ctx(BULLISH_BARS, NEUTRAL_END, position))

    assert signal is None


def test_killzone_filter_blocks_entries_outside_session() -> None:
    strategy = IctSweepFvgStrategy(_params(use_killzones=True))

    assert strategy.on_candle(_ctx(BULLISH_BARS, NEUTRAL_END)) is None


def test_killzone_filter_allows_entries_inside_session() -> None:
    strategy = IctSweepFvgStrategy(_params(use_killzones=True))

    signal = strategy.on_candle(_ctx(BULLISH_BARS, KILLZONE_END))

    assert signal is not None
    assert signal.action == "buy"


def test_no_signal_without_liquidity_sweep() -> None:
    bars = list(BULLISH_BARS)
    bars[26] = (100.0, 100.5, 96.0, 99.5)  # la mecha ya no perfora el swing low de 95

    assert IctSweepFvgStrategy(_params()).on_candle(_ctx(bars, NEUTRAL_END)) is None


def test_no_signal_when_fvg_was_already_tapped() -> None:
    bars = list(BULLISH_BARS)
    bars[-2] = (105.0, 106.0, 101.0, 105.5)  # una vela previa ya habia tocado el FVG

    assert IctSweepFvgStrategy(_params()).on_candle(_ctx(bars, NEUTRAL_END)) is None
