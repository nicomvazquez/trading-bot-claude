import datetime as dt

import pandas as pd
from pydantic import BaseModel

from app.backtest.engine import Backtester
from app.backtest.metrics import compute_metrics
from app.backtest.monte_carlo import run_monte_carlo
from app.strategies.base import Signal, Strategy, StrategyContext


class _AlternatingParams(BaseModel):
    pass


class _AlternatingStrategy(Strategy):
    """Estrategia de prueba: compra en velas pares sin posicion, cierra en
    las impares con posicion. Sirve para validar el motor sin red ni datos
    reales de Bybit."""

    key = "test_alternating"
    display_name = "Test"
    params_model = _AlternatingParams

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        i = len(ctx.candles) - 1
        if ctx.position is None and i % 4 == 0:
            return Signal(action="buy", stop_loss=ctx.candles["close"].iloc[-1] * 0.9, risk_pct=1.0)
        if ctx.position is not None and i % 4 == 2:
            return Signal(action="close")
        return None


def _make_candles(n: int = 200) -> pd.DataFrame:
    start = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    prices = [100 + (i % 20) - (i % 7) for i in range(n)]  # oscila, ni monotonamente sube ni baja
    index = [start + dt.timedelta(hours=i) for i in range(n)]
    return pd.DataFrame(
        {"open": prices, "high": [p + 1 for p in prices], "low": [p - 1 for p in prices],
         "close": prices, "volume": [1.0] * n},
        index=pd.DatetimeIndex(index, name="timestamp"),
    )


def test_backtester_produces_trades_and_equity_curve() -> None:
    candles = _make_candles()
    strategy = _AlternatingStrategy(_AlternatingParams())

    result = Backtester(fee_pct=0.05, min_lookback=10).run(strategy, candles, initial_capital=1000.0)

    assert len(result.trades) > 0
    assert len(result.equity_curve) == len(candles) - 10
    assert all(t.exit_time is not None for t in result.trades[:-1])  # todas menos quiza la ultima


def test_compute_metrics_has_expected_keys() -> None:
    candles = _make_candles()
    strategy = _AlternatingStrategy(_AlternatingParams())
    result = Backtester(fee_pct=0.05, min_lookback=10).run(strategy, candles, initial_capital=1000.0)

    metrics = compute_metrics(result, timeframe="60")

    for key in ("total_return_pct", "max_drawdown_pct", "sharpe_ratio", "num_trades", "win_rate_pct"):
        assert key in metrics


class _OneShotStrategy(Strategy):
    """Emite una unica compra con stop y take profit fijos en la primera vela."""

    key = "test_one_shot"
    display_name = "Test one shot"
    params_model = _AlternatingParams

    def __init__(self, params: BaseModel, stop_loss: float, take_profit: float, risk_pct: float = 1.0) -> None:
        super().__init__(params)
        self._sent = False
        self._sl, self._tp, self._risk = stop_loss, take_profit, risk_pct

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        if self._sent:
            return None
        self._sent = True
        return Signal(action="buy", stop_loss=self._sl, take_profit=self._tp, risk_pct=self._risk)


def _flat_candles(n: int = 30, price: float = 100.0) -> pd.DataFrame:
    start = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    index = pd.DatetimeIndex([start + dt.timedelta(hours=i) for i in range(n)], name="timestamp")
    return pd.DataFrame(
        {"open": [price] * n, "high": [price] * n, "low": [price] * n, "close": [price] * n, "volume": [1.0] * n},
        index=index,
    )


def _run_one_shot(candles: pd.DataFrame, sl: float, tp: float, **kwargs):
    strategy = _OneShotStrategy(_AlternatingParams(), sl, tp, **kwargs)
    return Backtester(fee_pct=0.0, min_lookback=5).run(strategy, candles, initial_capital=1000.0)


def test_entry_fills_at_next_candle_open() -> None:
    candles = _flat_candles()
    candles.iloc[6, candles.columns.get_loc("open")] = 101.0  # la senal sale al cierre de la vela 5

    result = _run_one_shot(candles, sl=90.0, tp=120.0)

    assert result.trades[0].entry_price == 101.0


def test_stop_loss_is_triggered_intracandle() -> None:
    candles = _flat_candles()
    candles.iloc[12, candles.columns.get_loc("low")] = 94.0

    result = _run_one_shot(candles, sl=95.0, tp=120.0)

    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == 95.0
    assert trade.pnl < 0


def test_take_profit_is_triggered_intracandle() -> None:
    candles = _flat_candles()
    candles.iloc[12, candles.columns.get_loc("high")] = 111.0

    result = _run_one_shot(candles, sl=90.0, tp=110.0)

    trade = result.trades[0]
    assert trade.exit_reason == "take_profit"
    assert trade.exit_price == 110.0
    assert trade.pnl > 0


def test_stop_wins_when_both_levels_hit_in_same_candle() -> None:
    candles = _flat_candles()
    candles.iloc[12, candles.columns.get_loc("high")] = 115.0
    candles.iloc[12, candles.columns.get_loc("low")] = 88.0

    result = _run_one_shot(candles, sl=95.0, tp=110.0)

    assert result.trades[0].exit_reason == "stop_loss"


def test_gap_through_stop_exits_at_open() -> None:
    candles = _flat_candles()
    for col in ("open", "high", "low", "close"):
        candles.iloc[12:, candles.columns.get_loc(col)] = 90.0  # gap bajista por debajo del stop

    result = _run_one_shot(candles, sl=95.0, tp=120.0)

    assert result.trades[0].exit_price == 90.0


def test_position_size_is_capped_by_max_leverage() -> None:
    candles = _flat_candles()
    strategy = _OneShotStrategy(_AlternatingParams(), stop_loss=99.99, take_profit=120.0)

    result = Backtester(fee_pct=0.0, min_lookback=5, max_leverage=5.0).run(
        strategy, candles, initial_capital=1000.0
    )

    trade = result.trades[0]
    assert trade.qty * trade.entry_price <= 1000.0 * 5.0 + 1e-6


def test_monte_carlo_returns_none_with_too_few_trades() -> None:
    candles = _make_candles(n=40)
    strategy = _AlternatingStrategy(_AlternatingParams())
    result = Backtester(fee_pct=0.05, min_lookback=10).run(strategy, candles, initial_capital=1000.0)

    mc = run_monte_carlo(result.trades, initial_capital=1000.0, n_sims=100, seed=1)

    assert mc is None  # con pocas velas hay muy pocos trades cerrados


def test_monte_carlo_shuffle_preserves_final_return() -> None:
    candles = _make_candles(n=400)
    strategy = _AlternatingStrategy(_AlternatingParams())
    result = Backtester(fee_pct=0.0, min_lookback=10).run(strategy, candles, initial_capital=1000.0)

    mc = run_monte_carlo(result.trades, initial_capital=1000.0, n_sims=200, method="shuffle", seed=1)

    assert mc is not None
    # shuffle reordena las mismas operaciones: el retorno final es siempre
    # el mismo (el producto de factores es conmutativo), solo cambia el camino
    assert abs(mc.return_pct_p5 - mc.return_pct_p95) < 0.01
