"""Ejecucion del motor: costos, ordenes, funding, sizing, intravela y ausencia de look-ahead."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from app.backtest.config import ConfigError, ExecutionConfig, RiskConfig
from app.backtest.engine import Backtester, DataError
from app.strategies import registry
from app.strategies.base import Signal, Strategy, StrategyContext

T0 = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
HOUR = dt.timedelta(hours=1)


class _NoParams(BaseModel):
    pass


class OneShot(Strategy):
    """Una unica senal en la primera vela que ve (min_lookback=5 => sale al cierre de la vela 5)."""

    key = "test_one_shot"
    display_name = "one shot"
    params_model = _NoParams

    def __init__(self, action="buy", stop_loss=95.0, take_profit=110.0, risk_pct=1.0, limit_price=None):
        super().__init__(_NoParams())
        self.signal = Signal(action=action, stop_loss=stop_loss, take_profit=take_profit,
                             risk_pct=risk_pct, limit_price=limit_price)
        self.sent = False

    def on_candle(self, ctx: StrategyContext):
        if self.sent:
            return None
        self.sent = True
        return self.signal


def candles(n=30, price=100.0, step=HOUR) -> pd.DataFrame:
    idx = pd.DatetimeIndex([T0 + i * step for i in range(n)], name="timestamp")
    return pd.DataFrame({"open": price, "high": price, "low": price, "close": price, "volume": 1.0}, index=idx)


def set_bar(df: pd.DataFrame, i: int, **ohlc) -> None:
    for col, value in ohlc.items():
        df.iloc[i, df.columns.get_loc(col)] = value


def run(strategy, df, execution=None, risk=None, capital=1000.0, **kwargs):
    bt = Backtester(execution=execution or ExecutionConfig(taker_fee_pct=0.0), risk=risk or RiskConfig(),
                    min_lookback=5, **kwargs)
    return bt.run(strategy, df, capital)


# ---------------------------------------------------------------- fees / PnL

def test_fees_and_pnl_known_case_with_maker_take_profit() -> None:
    df = candles()
    set_bar(df, 12, high=111.0)
    result = run(OneShot(), df, ExecutionConfig(taker_fee_pct=0.1, maker_fee_pct=0.02))

    t = result.trades[0]
    # riesgo 1% de 1000 = 10; distancia al stop 5 => 2 unidades
    assert t.qty == pytest.approx(2.0)
    assert t.entry_fee == pytest.approx(2 * 100 * 0.1 / 100)  # taker en la entrada: 0.2
    assert t.exit_price == 110.0 and t.exit_reason == "take_profit"
    assert t.exit_fee == pytest.approx(2 * 110 * 0.02 / 100)  # maker en el TP: 0.044
    assert t.gross_pnl == pytest.approx(20.0)
    assert t.pnl == pytest.approx(20.0 - 0.2 - 0.044)
    assert result.equity_curve.iloc[-1] == pytest.approx(1000 + t.pnl)


def test_sum_of_net_pnl_equals_equity_change_with_all_costs() -> None:
    df = candles(80)
    set_bar(df, 12, high=111.0)
    exe = ExecutionConfig(taker_fee_pct=0.1, maker_fee_pct=0.02, slippage_bps=5, spread_bps=4,
                          funding_mode="constant", funding_rate_pct=0.05)
    result = run(OneShot(), df, exe)
    assert sum(t.pnl for t in result.trades) == pytest.approx(result.equity_curve.iloc[-1] - 1000.0)


def test_stop_loss_pays_taker_fee_and_take_profit_pays_maker() -> None:
    df = candles()
    set_bar(df, 12, low=90.0)
    t = run(OneShot(), df, ExecutionConfig(taker_fee_pct=0.1, maker_fee_pct=0.0)).trades[0]
    assert t.exit_reason == "stop_loss"
    assert t.exit_fee == pytest.approx(t.qty * 95.0 * 0.1 / 100)


# ------------------------------------------------------------------ slippage

def test_slippage_moves_fills_against_the_trader() -> None:
    df = candles()
    set_bar(df, 12, low=90.0)
    t = run(OneShot(), df, ExecutionConfig(taker_fee_pct=0.0, slippage_bps=10)).trades[0]
    assert t.entry_price == pytest.approx(100.1)  # compra mas cara
    assert t.exit_price == pytest.approx(95 * 0.999)  # el stop vende mas barato
    assert t.slippage_cost > 0


def test_half_the_spread_is_paid_on_market_orders() -> None:
    df = candles()
    a = run(OneShot(), df, ExecutionConfig(taker_fee_pct=0.0, spread_bps=20)).trades[0]
    b = run(OneShot(), df, ExecutionConfig(taker_fee_pct=0.0, slippage_bps=10)).trades[0]
    assert a.entry_price == pytest.approx(b.entry_price)


def test_short_slippage_is_also_adverse() -> None:
    df = candles()
    t = run(OneShot("sell", 105.0, 90.0), df, ExecutionConfig(taker_fee_pct=0.0, slippage_bps=10)).trades[0]
    assert t.entry_price == pytest.approx(99.9)  # vende mas barato


# ------------------------------------------------------------- order types

def test_market_order_fills_at_next_open_not_at_signal_close() -> None:
    df = candles()
    set_bar(df, 6, open=103.0, high=103.0, low=103.0, close=103.0)
    t = run(OneShot(stop_loss=95.0, take_profit=None), df).trades[0]
    assert t.entry_price == 103.0 and t.entry_time == df.index[6]


def test_same_close_model_fills_at_the_signal_bar_close() -> None:
    df = candles()
    set_bar(df, 5, close=102.0, high=102.0)
    set_bar(df, 6, open=103.0, high=103.0, low=103.0, close=103.0)
    t = run(OneShot(take_profit=None), df, ExecutionConfig(taker_fee_pct=0.0, execution_model="same_close")).trades[0]
    assert t.entry_price == 102.0


def test_limit_order_fills_at_limit_price_when_touched_and_pays_maker() -> None:
    df = candles()
    set_bar(df, 6, open=101.0, high=102.0, low=99.5, close=101.0)
    exe = ExecutionConfig(taker_fee_pct=0.1, maker_fee_pct=0.0, order_type="limit")
    t = run(OneShot(take_profit=None), df, exe).trades[0]
    assert t.entry_price == 100.0  # limite = cierre de la vela de la senal
    assert t.entry_fee == 0.0


def test_limit_order_that_never_fills_is_lost_and_reported() -> None:
    df = candles()
    for i in range(6, 30):
        set_bar(df, i, open=105.0, high=105.0, low=105.0, close=105.0)
    result = run(OneShot(take_profit=None), df, ExecutionConfig(taker_fee_pct=0.0, order_type="limit"))
    assert result.trades == []
    assert result.diagnostics["limit_orders_expired"] == 1


def test_limit_order_stays_alive_for_ttl_bars() -> None:
    df = candles()
    for i in range(6, 30):
        set_bar(df, i, open=105.0, high=105.0, low=105.0, close=105.0)
    set_bar(df, 8, low=99.0)
    exe = ExecutionConfig(taker_fee_pct=0.0, order_type="limit", limit_ttl_bars=3)
    t = run(OneShot(take_profit=None), df, exe).trades[0]
    assert t.entry_time == df.index[8] and t.entry_price == 100.0


def test_stop_already_breached_at_open_skips_the_entry() -> None:
    df = candles()
    set_bar(df, 6, open=94.0, high=94.0, low=94.0, close=94.0)
    result = run(OneShot(stop_loss=95.0), df)
    assert result.trades == [] and result.diagnostics["skipped_signals"]["stop_ya_superado_al_abrir"] == 1


# ------------------------------------------------------------------ funding

def test_constant_funding_is_paid_by_longs_and_received_by_shorts() -> None:
    df = candles(20)  # velas de 1h desde 00:00 UTC: el funding cae a las 08:00 y 16:00
    set_bar(df, 10, high=111.0, low=99.0)
    exe = ExecutionConfig(taker_fee_pct=0.0, funding_mode="constant", funding_rate_pct=0.01)
    long_trade = run(OneShot(), df, exe).trades[0]
    assert long_trade.funding == pytest.approx(-2 * 100 * 0.0001)  # 2 unidades, 1 evento a las 08:00

    df_short = candles(20)
    set_bar(df_short, 10, low=89.0, high=101.0)
    short_trade = run(OneShot("sell", 105.0, 90.0), df_short, exe).trades[0]
    assert short_trade.funding == pytest.approx(+2 * 100 * 0.0001)


def test_historical_funding_uses_the_real_rates() -> None:
    df = candles(20)
    set_bar(df, 10, high=111.0)
    rates = pd.Series([0.0005], index=pd.DatetimeIndex([T0 + 8 * HOUR]))
    exe = ExecutionConfig(taker_fee_pct=0.0, funding_mode="historical")
    t = run(OneShot(), df, exe, funding_rates=rates).trades[0]
    assert t.funding == pytest.approx(-2 * 100 * 0.0005)


def test_no_funding_without_open_position_or_when_disabled() -> None:
    df = candles(20)
    set_bar(df, 10, high=111.0)
    assert run(OneShot(), df).trades[0].funding == 0.0


# ------------------------------------------------------------------- sizing

def test_fixed_notional_sizing_ignores_the_stop() -> None:
    t = run(OneShot(take_profit=None), candles(), risk=RiskConfig(sizing_mode="fixed_notional_pct", notional_pct_of_equity=50)).trades[0]
    assert t.qty == pytest.approx(1000 * 0.5 / 100)


def test_risk_override_replaces_the_strategy_risk() -> None:
    t = run(OneShot(risk_pct=1.0, take_profit=None), candles(), risk=RiskConfig(risk_per_trade_pct=2.0)).trades[0]
    assert t.qty == pytest.approx(20 / 5)


def test_leverage_is_a_cap_not_the_position_size() -> None:
    # stop lejano: el tamano lo define el riesgo (2 unidades = 200 de nocional), el tope de 1x (1000) no actua
    normal = run(OneShot(take_profit=None), candles(), risk=RiskConfig(max_leverage=1)).trades[0]
    assert normal.qty == pytest.approx(2.0) and not normal.capped_by_leverage
    # stop casi pegado: el riesgo pediria 1000 unidades; el tope de 1x lo limita a 10 (nocional 1000)
    tight = run(OneShot(stop_loss=99.99, take_profit=None), candles(), risk=RiskConfig(max_leverage=1))
    assert tight.trades[0].qty == pytest.approx(10.0) and tight.trades[0].capped_by_leverage
    assert tight.diagnostics["capped_by_leverage"] == 1


def test_max_position_pct_caps_the_notional() -> None:
    t = run(OneShot(stop_loss=99.99, take_profit=None), candles(), risk=RiskConfig(max_leverage=10, max_position_pct_of_equity=50)).trades[0]
    assert t.qty * t.entry_price == pytest.approx(500.0)


def test_risk_can_include_costs_in_the_stop_distance() -> None:
    exe = ExecutionConfig(taker_fee_pct=0.1)
    plain = run(OneShot(take_profit=None), candles(), exe).trades[0]
    with_costs = run(OneShot(take_profit=None), candles(), exe, risk=RiskConfig(risk_includes_costs=True)).trades[0]
    assert with_costs.qty < plain.qty


# --------------------------------------------------------------- intrabar

def _intrabar_setup(sub_first_hits_tp: bool):
    df = candles(20)
    set_bar(df, 10, high=111.0, low=94.0)  # esa vela toca stop (95) y take profit (110)
    idx = pd.DatetimeIndex([df.index[10] + i * dt.timedelta(minutes=5) for i in range(12)])
    sub = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}, index=idx)
    if sub_first_hits_tp:
        sub.iloc[2, sub.columns.get_loc("high")] = 111.0
        sub.iloc[5, sub.columns.get_loc("low")] = 94.0
    else:
        sub.iloc[2, sub.columns.get_loc("low")] = 94.0
        sub.iloc[5, sub.columns.get_loc("high")] = 111.0
    return df, sub


def test_ambiguous_bar_assumes_stop_first_without_intrabar_data() -> None:
    df, _ = _intrabar_setup(True)
    assert run(OneShot(), df).trades[0].exit_reason == "stop_loss"


def test_intrabar_resolution_uses_lower_timeframe_order() -> None:
    df, sub = _intrabar_setup(sub_first_hits_tp=True)
    exe = ExecutionConfig(taker_fee_pct=0.0, intrabar_resolution=True)
    result = run(OneShot(), df, exe, intrabar_candles=sub)
    assert result.trades[0].exit_reason == "take_profit" and result.diagnostics["intrabar_resolved"] == 1

    df2, sub2 = _intrabar_setup(sub_first_hits_tp=False)
    assert run(OneShot(), df2, exe, intrabar_candles=sub2).trades[0].exit_reason == "stop_loss"


# ------------------------------------------------- equity / open positions

def test_equity_is_cash_plus_unrealized_pnl() -> None:
    df = candles()
    set_bar(df, 10, open=100.0, high=103.0, low=100.0, close=103.0)
    exe = ExecutionConfig(taker_fee_pct=0.1)
    result = run(OneShot(stop_loss=50.0, take_profit=None), df, exe)
    qty = 0.2  # 10 de riesgo / 50 de distancia
    expected = 1000 - qty * 100 * 0.001 + (103 - 100) * qty
    assert result.equity_curve.loc[df.index[10]] == pytest.approx(expected)


def test_position_open_at_the_end_is_closed_and_flagged() -> None:
    df = candles()
    result = run(OneShot(stop_loss=50.0, take_profit=None), df)
    t = result.trades[0]
    assert t.open_at_end and t.exit_reason == "fin del backtest" and t.exit_time == df.index[-1]
    assert result.diagnostics["open_at_end"] is True


# --------------------------------------------------------- input validation

def test_invalid_candles_are_rejected() -> None:
    df = candles()
    set_bar(df, 3, close=float("nan"))
    with pytest.raises(DataError):
        run(OneShot(), df)
    with pytest.raises(DataError):
        run(OneShot(), candles(0))


@pytest.mark.parametrize("exe, risk", [
    (ExecutionConfig(taker_fee_pct=-0.1), RiskConfig()),
    (ExecutionConfig(slippage_bps=-1), RiskConfig()),
    (ExecutionConfig(), RiskConfig(max_leverage=0)),
    (ExecutionConfig(), RiskConfig(max_leverage=500)),
    (ExecutionConfig(), RiskConfig(risk_per_trade_pct=0)),
])
def test_invalid_configuration_is_rejected(exe, risk) -> None:
    with pytest.raises(ConfigError):
        run(OneShot(), candles(), exe, risk)


def test_non_positive_capital_is_rejected() -> None:
    with pytest.raises(ConfigError):
        run(OneShot(), candles(), capital=0)


# ------------------------------------------------------------- no look-ahead

class Recorder(Strategy):
    key = "test_recorder"
    display_name = "recorder"
    params_model = _NoParams

    def __init__(self):
        super().__init__(_NoParams())
        self.seen: list[pd.Timestamp] = []

    def on_candle(self, ctx: StrategyContext):
        self.seen.append(ctx.candles.index[-1])
        return None


def test_strategy_only_sees_candles_up_to_the_current_close() -> None:
    df = candles(40)
    rec = Recorder()
    run(rec, df)
    # min_lookback=5 => primera decision en la vela 5; nunca ve una vela posterior a la actual
    assert rec.seen == list(df.index[5:39])


def test_trade_start_only_warms_up_indicators() -> None:
    df = candles(60)
    seen = Recorder()
    bt = Backtester(execution=ExecutionConfig(taker_fee_pct=0.0), min_lookback=5)
    result = bt.run(seen, df, 1000.0, trade_start=df.index[30])
    assert len(result.equity_curve) == 30  # solo se simula desde trade_start
    assert seen.seen[0] == df.index[30]
    assert len(seen.seen) == 29  # la ultima vela no genera decision


def _random_walk(n=3000, seed=11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.concatenate(([100.0], close[:-1]))
    spread = np.abs(rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    idx = pd.DatetimeIndex([T0 + i * dt.timedelta(minutes=15) for i in range(n)], name="timestamp")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)


def _strategies():
    sma = registry.get("sma_cross")(registry.get("sma_cross").params_model(fast_period=5, slow_period=20))
    rsi = registry.get("rsi_reversion")(registry.get("rsi_reversion").params_model(rsi_period=5, oversold=35, overbought=65))
    ict = registry.get("ict_sweep_fvg")(registry.get("ict_sweep_fvg").params_model(use_killzones=False))
    return {"sma_cross": sma, "rsi_reversion": rsi, "ict_sweep_fvg": ict}


def _fresh(key: str):
    cls = registry.get(key)
    params = {
        "sma_cross": dict(fast_period=5, slow_period=20),
        "rsi_reversion": dict(rsi_period=5, oversold=35, overbought=65),
        "ict_sweep_fvg": dict(use_killzones=False),
    }[key]
    return cls(cls.params_model(**params))


@pytest.mark.parametrize("key", ["sma_cross", "rsi_reversion", "ict_sweep_fvg"])
def test_no_lookahead_truncating_future_data_does_not_change_past_entries(key) -> None:
    """Si una estrategia usara datos futuros, cortar el futuro cambiaria sus
    entradas pasadas. Las entradas anteriores al corte deben ser identicas."""
    df = _random_walk()
    cut = 2200
    bt = Backtester(execution=ExecutionConfig(taker_fee_pct=0.0), min_lookback=50)
    full = bt.run(_fresh(key), df, 1000.0)
    part = bt.run(_fresh(key), df.iloc[:cut], 1000.0)

    limit = df.index[cut - 1]
    entries = lambda r: [(t.entry_time, t.side, round(t.entry_price, 8)) for t in r.trades if t.entry_time <= limit]
    assert entries(full) == entries(part)
    assert len(entries(full)) >= 3  # el test no es vacuo: cada estrategia opera antes del corte


class Cheater(Strategy):
    """Espia 3 velas hacia el futuro leyendo el DataFrame completo (lo que una estrategia con look-ahead haria)."""

    key = "test_cheater"
    display_name = "cheater"
    params_model = _NoParams

    def __init__(self, full: pd.DataFrame):
        super().__init__(_NoParams())
        self.full = full

    def on_candle(self, ctx: StrategyContext):
        i = len(ctx.candles) - 1
        j = min(i + 3, len(self.full) - 1)
        if ctx.position is None and self.full["close"].iloc[j] > self.full["close"].iloc[i]:
            return Signal(action="buy", stop_loss=self.full["close"].iloc[i] * 0.9)
        if ctx.position is not None and self.full["close"].iloc[j] < self.full["close"].iloc[i]:
            return Signal(action="close")
        return None


def test_the_lookahead_detector_catches_a_strategy_that_peeks_into_the_future() -> None:
    df = _random_walk()
    cut = 2200
    bt = Backtester(execution=ExecutionConfig(taker_fee_pct=0.0), min_lookback=50)
    full = bt.run(Cheater(df), df, 1000.0)
    part_df = df.iloc[:cut]
    part = bt.run(Cheater(part_df), part_df, 1000.0)

    limit = df.index[cut - 1]
    entries = lambda r: [(t.entry_time, t.side, round(t.entry_price, 8)) for t in r.trades if t.entry_time <= limit]
    assert entries(full) != entries(part)  # cortar el futuro cambia las entradas: el detector funciona


# ------------------------------------------------------------------- estres: stops con gap y funding adverso
def test_stop_slippage_only_hurts_stop_exits_not_take_profit_or_entries() -> None:
    df = candles()
    set_bar(df, 12, low=90.0)  # se activa el stop en 95
    base = run(OneShot(), df, ExecutionConfig(taker_fee_pct=0.0)).trades[0]
    worse = run(OneShot(), df, ExecutionConfig(taker_fee_pct=0.0, stop_slippage_bps=100)).trades[0]
    assert base.exit_price == pytest.approx(95.0)
    assert worse.exit_price == pytest.approx(95.0 * 0.99)        # 1% peor, solo en el stop
    assert worse.entry_price == base.entry_price                  # la entrada no cambia
    assert worse.pnl < base.pnl

    df_tp = candles()
    set_bar(df_tp, 12, high=111.0)  # se activa el take-profit en 110
    a = run(OneShot(), df_tp, ExecutionConfig(taker_fee_pct=0.0)).trades[0]
    b = run(OneShot(), df_tp, ExecutionConfig(taker_fee_pct=0.0, stop_slippage_bps=100)).trades[0]
    assert a.exit_price == b.exit_price == pytest.approx(110.0)   # el take-profit no se ve afectado


def test_stop_slippage_is_adverse_for_shorts_too() -> None:
    df = candles()
    set_bar(df, 12, high=110.0)  # el corto se para en 105
    base = run(OneShot("sell", 105.0, 90.0), df, ExecutionConfig(taker_fee_pct=0.0)).trades[0]
    worse = run(OneShot("sell", 105.0, 90.0), df, ExecutionConfig(taker_fee_pct=0.0, stop_slippage_bps=100)).trades[0]
    assert worse.exit_price == pytest.approx(base.exit_price * 1.01)  # comprar de vuelta mas caro


def test_adverse_funding_is_a_cost_for_longs_and_for_shorts() -> None:
    exe = ExecutionConfig(taker_fee_pct=0.0, funding_mode="constant", funding_rate_pct=0.01, funding_adverse=True)
    df = candles(20)
    set_bar(df, 10, high=111.0, low=99.0)
    assert run(OneShot(), df, exe).trades[0].funding == pytest.approx(-2 * 100 * 0.0001)
    df_short = candles(20)
    set_bar(df_short, 10, low=89.0, high=101.0)
    short = run(OneShot("sell", 105.0, 90.0), df_short, exe).trades[0]
    assert short.funding == pytest.approx(-2 * 100 * 0.0001)  # en el modo normal el corto COBRARIA +0.02


def test_adverse_funding_uses_the_absolute_value_of_negative_rates() -> None:
    df = candles(20)
    set_bar(df, 10, high=111.0)
    rates = pd.Series([-0.0005], index=pd.DatetimeIndex([T0 + 8 * HOUR]))  # los largos cobrarian
    normal = run(OneShot(), df, ExecutionConfig(taker_fee_pct=0.0, funding_mode="historical"), funding_rates=rates).trades[0]
    adverse = run(OneShot(), df, ExecutionConfig(taker_fee_pct=0.0, funding_mode="historical", funding_adverse=True), funding_rates=rates).trades[0]
    assert normal.funding == pytest.approx(+2 * 100 * 0.0005) and adverse.funding == pytest.approx(-2 * 100 * 0.0005)


def test_stress_options_are_off_by_default_and_validated() -> None:
    exe = ExecutionConfig()
    assert exe.stop_slippage_bps == 0.0 and exe.funding_adverse is False and exe.validate() == []
    assert ExecutionConfig(stop_slippage_bps=-1).validate()


def test_old_saved_configs_without_the_new_fields_still_load() -> None:
    from app.backtest.config import BacktestConfig

    data = BacktestConfig().to_dict()
    del data["execution"]["stop_slippage_bps"], data["execution"]["funding_adverse"]  # una corrida guardada antes de esta version
    cfg = BacktestConfig.from_dict(data)
    assert cfg.execution.stop_slippage_bps == 0.0 and cfg.execution.funding_adverse is False
