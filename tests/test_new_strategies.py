import numpy as np
import pandas as pd
import pytest

from app.backtest.config import BacktestConfig
from app.backtest.engine import Backtester
from app.strategies import registry
from app.strategies.base import Position, StrategyContext
from app.strategies.examples.donchian_breakout import DonchianBreakoutStrategy, DonchianParams
from app.strategies.examples.trend_pullback import TrendPullbackParams, TrendPullbackStrategy
from app.strategies.examples.volatility_squeeze import SqueezeParams, VolatilitySqueezeStrategy
from app.strategies.indicators import atr_series, rolling_mean_std, rsi_wilder

T0 = pd.Timestamp("2026-01-01", tz="UTC")


def candles_from(closes, freq="1h", spread=0.002) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(T0, periods=len(closes), freq=freq)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame({
        "open": opens, "high": np.maximum(opens, closes) * (1 + spread), "low": np.minimum(opens, closes) * (1 - spread),
        "close": closes, "volume": 1.0,
    }, index=idx)


def signal(strategy, candles, position=None):
    return strategy.on_candle(StrategyContext(candles=candles, position=position, equity=1000))


# ------------------------------------------------------------------ indicadores vs referencia pandas

def test_indicators_match_pandas_references() -> None:
    rng = np.random.default_rng(5)
    c = candles_from(100 + np.cumsum(rng.normal(0, 1, 400)))
    high, low, close = (c[k].to_numpy() for k in ("high", "low", "close"))

    tr = pd.concat([c["high"] - c["low"], (c["high"] - c["close"].shift()).abs(), (c["low"] - c["close"].shift()).abs()], axis=1).max(axis=1)
    np.testing.assert_allclose(atr_series(high, low, close, 14), tr.iloc[1:].rolling(14).mean().dropna().to_numpy())  # la primera vela no tiene cierre previo

    mean, std = rolling_mean_std(close, 20)
    np.testing.assert_allclose(mean, c["close"].rolling(20).mean().dropna().to_numpy())
    np.testing.assert_allclose(std, c["close"].rolling(20).std(ddof=0).dropna().to_numpy())

    delta = c["close"].diff()
    gain, loss = delta.clip(lower=0), (-delta).clip(lower=0)
    avg_g = gain.iloc[1:].copy()
    avg_l = loss.iloc[1:].copy()
    # Wilder: semilla = media simple de los primeros 14, despues suavizado recursivo
    ref = []
    g, l = avg_g.iloc[:14].mean(), avg_l.iloc[:14].mean()
    ref.append(100 - 100 / (1 + g / l))
    for gi, li in zip(avg_g.iloc[14:], avg_l.iloc[14:]):
        g, l = (g * 13 + gi) / 14, (l * 13 + li) / 14
        ref.append(100 - 100 / (1 + g / l))
    np.testing.assert_allclose(rsi_wilder(close, 14), ref)


def test_rsi_degenerate_cases() -> None:
    assert rsi_wilder(np.full(30, 100.0), 14)[-1] == 50.0
    assert rsi_wilder(np.arange(100.0, 130.0), 14)[-1] == 100.0
    assert len(rsi_wilder(np.array([1.0, 2.0]), 14)) == 0


# ------------------------------------------------------------------ Donchian

def calm_then(n_calm=120, tail=()) -> np.ndarray:
    """Mercado lateral con oscilacion regular y luego el tramo `tail`."""
    base = 100 + np.sin(np.arange(n_calm) / 3.0) * 0.4
    return np.concatenate([base, np.asarray(tail, dtype=float)])


def test_donchian_buys_a_breakout_with_expanding_volatility_and_sets_an_atr_stop() -> None:
    closes = calm_then(tail=[100.5, 101.5, 103.5, 106.0])  # velas grandes: el ATR se expande
    c = candles_from(closes)
    sig = signal(DonchianBreakoutStrategy(DonchianParams()), c)
    assert sig is not None and sig.action == "buy"
    assert sig.stop_loss < 106.0 and sig.take_profit is None  # deja correr: sin take profit


def test_donchian_ignores_the_breakout_when_the_volatility_filter_is_not_met() -> None:
    closes = calm_then(tail=[100.5, 101.5, 103.5, 106.0])
    sig = signal(DonchianBreakoutStrategy(DonchianParams(min_vol_ratio=5.0)), candles_from(closes))
    assert sig is None


def test_donchian_shorts_the_downside_and_respects_direction_switches() -> None:
    closes = calm_then(tail=[99.5, 98.5, 96.5, 94.0])
    c = candles_from(closes)
    assert signal(DonchianBreakoutStrategy(DonchianParams()), c).action == "sell"
    assert signal(DonchianBreakoutStrategy(DonchianParams(allow_short=False)), c) is None


def test_donchian_exits_when_the_short_channel_breaks_against_the_position() -> None:
    closes = calm_then(tail=[101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 100.0])
    pos = Position(side="long", entry_price=101.0, qty=1.0)
    sig = signal(DonchianBreakoutStrategy(DonchianParams()), candles_from(closes), pos)
    assert sig is not None and sig.action == "close"
    holding = signal(DonchianBreakoutStrategy(DonchianParams()), candles_from(closes[:-1]), pos)
    assert holding is None


# ------------------------------------------------------------------ Squeeze

def noisy_then_quiet_then(tail) -> np.ndarray:
    rng = np.random.default_rng(1)
    noisy = 100 + np.cumsum(rng.normal(0, 1.2, 100))
    quiet = np.full(40, noisy[-1]) + rng.normal(0, 0.02, 40)
    return np.concatenate([noisy, quiet, np.asarray(tail, dtype=float) + noisy[-1]])


def test_squeeze_buys_the_expansion_after_a_compression() -> None:
    closes = noisy_then_quiet_then([1.0])
    sig = signal(VolatilitySqueezeStrategy(SqueezeParams()), candles_from(closes))
    assert sig is not None and sig.action == "buy" and sig.stop_loss < closes[-1]
    down = signal(VolatilitySqueezeStrategy(SqueezeParams()), candles_from(noisy_then_quiet_then([-1.0])))
    assert down is not None and down.action == "sell"


def test_squeeze_needs_a_recent_compression() -> None:
    rng = np.random.default_rng(2)
    closes = 100 + np.cumsum(rng.normal(0, 1.0, 200))  # volatilidad pareja: nunca hay compresion reciente clara
    closes[-1] = closes[-2] + 6.0
    sig = signal(VolatilitySqueezeStrategy(SqueezeParams(squeeze_pct=1)), candles_from(closes))
    assert sig is None


def test_squeeze_exits_on_return_to_the_mean() -> None:
    closes = noisy_then_quiet_then([1.0, 1.5, 0.2])
    pos = Position(side="long", entry_price=100.0, qty=1.0)
    c = candles_from(closes)
    c.iloc[-1, c.columns.get_loc("close")] = closes[-4]  # vuelve al nivel de la compresion (bajo la media movil)
    sig = signal(VolatilitySqueezeStrategy(SqueezeParams()), c, pos)
    assert sig is not None and sig.action == "close"


# ------------------------------------------------------------------ Tendencia multi-timeframe

def uptrend_with_dip(n_trend=900, dip=14, rebound=12) -> np.ndarray:
    rng = np.random.default_rng(4)
    up = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.002, n_trend)))
    dip_path = up[-1] * np.exp(np.cumsum(np.full(dip, -0.004)))
    reb = dip_path[-1] * np.exp(np.cumsum(np.full(rebound, 0.006)))
    return np.concatenate([up, dip_path, reb])


def test_pullback_trend_uses_only_completed_higher_timeframe_bars() -> None:
    s = TrendPullbackStrategy(TrendPullbackParams())
    c = candles_from(uptrend_with_dip(), freq="15min")
    base = pd.Timedelta("15min")
    # una vela base a mitad de una vela de 4h: la vela de 4h en formacion no puede influir
    mid = next(i for i in range(len(c) - 1, 0, -1) if c.index[i].hour % 4 == 1 and c.index[i].minute == 30)
    view = c.iloc[: mid + 1].copy()
    trend = s._trend(view, base)
    changed = view.copy()
    changed.iloc[-1, changed.columns.get_loc("close")] *= 0.5  # desplome en la vela actual
    assert TrendPullbackStrategy(TrendPullbackParams())._trend(changed, base) == trend
    # en la ultima subvela de la barra superior (ya completa) SI se incorpora: un desplome de ese cierre cambia la tendencia
    last_sub = next(i for i in range(mid, 0, -1) if c.index[i].hour % 4 == 3 and c.index[i].minute == 45)
    closed = c.iloc[: last_sub + 1].copy()
    crashed = closed.copy()
    crashed.iloc[-1, crashed.columns.get_loc("close")] *= 0.5
    assert TrendPullbackStrategy(TrendPullbackParams())._trend(closed, base) == 1
    assert TrendPullbackStrategy(TrendPullbackParams())._trend(crashed, base) == -1


def test_pullback_buys_when_rsi_recovers_inside_an_uptrend() -> None:
    c = candles_from(uptrend_with_dip(), freq="15min")
    s = TrendPullbackStrategy(TrendPullbackParams())
    signals = [signal(s, c.iloc[: i + 1]) for i in range(900, len(c))]
    buys = [x for x in signals if x is not None and x.action == "buy"]
    assert buys, "debe haber una entrada al terminar el retroceso"
    assert buys[0].stop_loss < buys[0].take_profit


def test_pullback_requires_a_smaller_timeframe_than_the_trend() -> None:
    c = candles_from(uptrend_with_dip(), freq="4h")
    assert signal(TrendPullbackStrategy(TrendPullbackParams()), c) is None


def test_pullback_closes_when_the_higher_timeframe_trend_flips() -> None:
    n = 900
    down = 200 * np.exp(-np.cumsum(np.full(n, 0.0009)))  # tendencia bajista clara
    c = candles_from(down, freq="15min")
    pos = Position(side="long", entry_price=200.0, qty=1.0)
    sig = signal(TrendPullbackStrategy(TrendPullbackParams()), c, pos)
    assert sig is not None and sig.action == "close"


# ------------------------------------------------------------------ dentro del motor

@pytest.mark.parametrize("key,freq,timeframe", [
    ("donchian_breakout", "1h", "60"),
    ("volatility_squeeze", "1h", "60"),
    ("trend_pullback", "15min", "15"),
])
def test_each_strategy_runs_in_the_backtester_and_pnl_reconciles(key, freq, timeframe) -> None:
    rng = np.random.default_rng(21)
    n = 4000
    regime = np.repeat(rng.choice([-0.0006, 0.0, 0.0008], size=n // 250 + 1), 250)[:n]  # tendencias y laterales
    closes = 100 * np.exp(np.cumsum(regime + rng.normal(0, 0.003, n)))
    strategy_cls = registry.get(key)
    cfg = BacktestConfig(timeframe=timeframe)
    result = Backtester(execution=cfg.execution, risk=cfg.risk).run(
        strategy_cls(strategy_cls.params_model()), candles_from(closes, freq=freq), 1000.0
    )
    assert len(result.trades) > 0, f"{key} no genero operaciones en datos con tendencias"
    assert result.equity_curve.iloc[-1] == pytest.approx(1000.0 + sum(t.pnl for t in result.trades))
    assert {t.side for t in result.trades} <= {"long", "short"}


def test_the_new_strategies_have_descriptions_and_valid_defaults() -> None:
    for key in ("donchian_breakout", "volatility_squeeze", "trend_pullback", "funding_oi"):
        cls = registry.get(key)
        assert cls.description and cls.display_name
        cls.params_model()  # los valores por defecto cumplen sus propias restricciones
