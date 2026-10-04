import numpy as np
import pandas as pd
import pytest

from app.strategies import registry
from app.strategies.base import StrategyContext
from app.strategies.price_action.sweep_mss import SweepMssParams, SweepMssStrategy

START = pd.Timestamp("2026-01-01 00:00", tz="UTC")


def _strategy(**overrides) -> SweepMssStrategy:
    return SweepMssStrategy(SweepMssParams(**overrides))


def _stream(strategy, candles: pd.DataFrame, start: int = 0):
    out = []
    for i in range(start, len(candles)):
        sig = strategy.on_candle(StrategyContext(candles=candles.iloc[: i + 1], position=None, equity=1000.0))
        if sig is not None:
            out.append((i, sig))
    return out


def _random_1m(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.05, n))
    idx = pd.date_range(START, periods=n, freq="1min")
    return pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],
            "high": close + rng.uniform(0, 0.05, n),
            "low": close - rng.uniform(0, 0.05, n),
            "close": close,
            "volume": rng.uniform(1, 5, n),
        },
        index=idx,
    )


def _expand_5m(bars: list[tuple[float, float, float, float]]) -> list[dict]:
    """Cada barra de 5 minutos se convierte en 5 velas de 1 minuto cuyo OHLC agregado es el de la barra."""
    rows = []
    for o, h, l, c in bars:
        prev = o
        for j in range(5):
            close = c if j == 4 else o + (c - o) * (j + 1) / 5
            high, low = max(prev, close), min(prev, close)
            if j == 2:
                high, low = max(high, h), min(low, l)
            rows.append({"open": prev, "high": high, "low": low, "close": close, "volume": 1.0})
            prev = close
    return rows


# Escenario alcista construido a mano (barras de 5 min): liquidez en 95 (swing low de la barra 21),
# máximo previo en 101 (barra 24). La barra 27 barre 95 con mecha a 94.7 y recupera; la 28 cierra
# sobre 101 (MSS); la 29 deja un FVG entre 98.8 y 101.5; la 30 no vuelve al FVG.
_WARMUP = [(100.0, 100.3, 99.7, 100.0)] * 20
_SCENARIO = _WARMUP + [
    (100.0, 100.4, 99.5, 99.9),   # 20
    (99.9, 100.0, 95.0, 96.0),    # 21: swing low 95
    (96.0, 97.0, 95.8, 96.5),     # 22
    (96.5, 98.0, 96.2, 97.8),     # 23
    (97.8, 101.0, 97.5, 100.5),   # 24: swing high 101
    (100.5, 100.6, 99.5, 99.8),   # 25
    (99.8, 100.0, 98.5, 98.6),    # 26
    (98.6, 98.8, 94.7, 97.0),     # 27: barrida de 95 y recuperación
    (97.0, 102.0, 96.9, 101.6),   # 28: cierre sobre 101 = MSS, desplazamiento
    (101.6, 105.0, 101.5, 104.8), # 29: completa el FVG (98.8 - 101.5)
    (104.8, 106.0, 101.8, 105.5), # 30: no vuelve al FVG
]


def _scenario_candles(last_1m: tuple[float, float, float, float]) -> pd.DataFrame:
    rows = _expand_5m(_SCENARIO)
    o, h, l, c = last_1m
    rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
    idx = pd.date_range(START, periods=len(rows), freq="1min")
    return pd.DataFrame(rows, index=idx)


def test_strategy_is_registered_with_the_shared_interface() -> None:
    cls = registry.get("sweep_mss")
    assert issubclass(cls, SweepMssStrategy)
    assert cls.default_timeframe == "1"
    assert cls.params_model is SweepMssParams


def test_signals_at_each_bar_never_depend_on_future_candles() -> None:
    base = _random_1m(1500, seed=7)
    cutoff = 1000
    altered = base.copy()
    rng = np.random.default_rng(99)
    noise = rng.normal(0, 3, len(altered) - cutoff)
    altered.iloc[cutoff:, altered.columns.get_loc("close")] += noise
    altered["high"] = np.maximum(altered["high"], altered["close"])
    altered["low"] = np.minimum(altered["low"], altered["close"])

    original = [i for i, _ in _stream(_strategy(), base, start=200) if i < cutoff]
    shifted = [i for i, _ in _stream(_strategy(), altered, start=200) if i < cutoff]
    assert original == shifted


def test_same_candles_always_give_the_same_signals() -> None:
    candles = _random_1m(1200, seed=3)
    first = [(i, s.action, s.stop_loss, s.take_profit) for i, s in _stream(_strategy(), candles)]
    second = [(i, s.action, s.stop_loss, s.take_profit) for i, s in _stream(_strategy(), candles)]
    assert first == second


def test_bullish_sweep_mss_fvg_and_retest_produce_one_long() -> None:
    candles = _scenario_candles(last_1m=(101.6, 102.0, 101.3, 101.9))  # la vela de retroceso toca el FVG
    signals = _stream(_strategy(), candles)
    assert len(signals) == 1
    idx, sig = signals[0]
    assert idx == len(candles) - 1
    assert sig.action == "buy"
    assert sig.stop_loss == pytest.approx(94.7 * (1 - 0.05 / 100))
    assert sig.take_profit == pytest.approx(101.9 + 2.0 * (101.9 - sig.stop_loss))
    assert "barrida" in sig.reason and "MSS" in sig.reason


def test_no_retest_no_trade() -> None:
    candles = _scenario_candles(last_1m=(102.0, 103.0, 101.9, 102.6))  # no baja hasta el FVG
    assert _stream(_strategy(), candles) == []



def test_mirrored_scenario_gives_the_mirrored_short() -> None:
    long_candles = _scenario_candles(last_1m=(101.6, 102.0, 101.3, 101.9))
    mirrored = pd.DataFrame(
        {
            "open": 200 - long_candles["open"],
            "high": 200 - long_candles["low"],
            "low": 200 - long_candles["high"],
            "close": 200 - long_candles["close"],
            "volume": long_candles["volume"],
        },
        index=long_candles.index,
    )
    short_sig = _stream(_strategy(), mirrored)[0][1]
    assert short_sig.action == "sell"
    sweep_high = 200 - 94.7
    assert short_sig.stop_loss == pytest.approx(sweep_high * (1 + 0.05 / 100))
    entry = 200 - 101.9
    assert short_sig.take_profit == pytest.approx(entry - 2.0 * (short_sig.stop_loss - entry))


def test_every_optional_filter_runs_without_errors() -> None:
    candles = _random_1m(2000, seed=11)
    strategy = _strategy(
        use_atr_filter=True, use_volume_filter=True, use_regime_filter=True,
        tp_at_opposite_liquidity=True, fvg_entry_midpoint=True,
    )
    signals = _stream(strategy, candles)
    for _, sig in signals:
        assert sig.stop_loss is not None and sig.take_profit is not None
