import datetime as dt
from dataclasses import dataclass

import pytest

from app.live.rules import (
    closing_side, decide_candle, find_symbol_conflict, should_adopt_position, summarize_closed_pnl, summarize_trade_costs,
)

T = dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc)


@dataclass
class Inst:
    id: int
    name: str
    symbol: str
    is_active: bool


# ------------------------------------------------------------------ un simbolo, una instancia

def test_conflict_detects_another_active_instance_on_the_same_symbol() -> None:
    instances = [Inst(1, "a", "BTCUSDT", True), Inst(2, "b", "ETHUSDT", True), Inst(3, "c", "BTCUSDT", False)]
    assert find_symbol_conflict(instances, "btcusdt").name == "a"          # sin importar mayusculas
    assert find_symbol_conflict(instances, "BTCUSDT", exclude_id=1) is None  # ella misma no cuenta
    assert find_symbol_conflict(instances, "SOLUSDT") is None
    assert find_symbol_conflict([Inst(3, "c", "BTCUSDT", False)], "BTCUSDT") is None  # las apagadas no cuentan


# ------------------------------------------------------------------ adopcion de posiciones

@pytest.mark.parametrize("local,exchange,elsewhere,expected", [
    (False, True, False, True),    # posicion huerfana: se adopta
    (False, True, True, False),    # es de otra instancia: NO se adopta (el bug que se corrige)
    (True, True, False, False),    # ya tiene su trade
    (False, False, False, False),  # no hay nada que adoptar
])
def test_adoption_rules(local, exchange, elsewhere, expected) -> None:
    assert should_adopt_position(local, exchange, elsewhere) is expected


# ------------------------------------------------------------------ velas

def test_first_candle_after_starting_is_not_evaluated() -> None:
    assert decide_candle(None, T) == "skip_initial"
    assert decide_candle(None, T, skip_initial=False) == "evaluate"


def test_only_a_newer_closed_candle_is_evaluated() -> None:
    assert decide_candle(T, T) == "wait"
    assert decide_candle(T, T + dt.timedelta(minutes=15)) == "evaluate"
    assert decide_candle(T, T - dt.timedelta(minutes=15)) == "wait"


# ------------------------------------------------------------------ PnL de cierre

def rec(minutes: int, pnl: float, price: float = 100.0) -> dict:
    return {"updated_time": T + dt.timedelta(minutes=minutes), "closed_pnl": pnl, "avg_exit_price": price}


def test_closed_pnl_ignores_records_from_before_the_trade_opened() -> None:
    records = [rec(-60, 50.0, 90.0), rec(10, -3.0, 95.0)]  # el primero es de un trade anterior
    result = summarize_closed_pnl(records, since=T)
    assert result.closed_pnl == pytest.approx(-3.0) and result.avg_exit_price == 95.0 and result.records == 1


def test_closed_pnl_sums_partial_closes_and_uses_the_last_exit_price() -> None:
    records = [rec(20, 1.5, 102.0), rec(10, 2.0, 101.0)]  # desordenados a proposito
    result = summarize_closed_pnl(records, since=T)
    assert result.closed_pnl == pytest.approx(3.5)
    assert result.avg_exit_price == 102.0 and result.updated_time == T + dt.timedelta(minutes=20)


def test_closed_pnl_is_none_when_nothing_closed_since_opening() -> None:
    assert summarize_closed_pnl([rec(-5, 9.0)], since=T) is None
    assert summarize_closed_pnl([], since=T) is None


def test_pandas_timestamps_work_with_decide_candle_and_the_start_message() -> None:
    """El runner recibe pd.Timestamp del indice de velas (no datetime): mismas reglas y mismo formato del mensaje."""
    import pandas as pd

    newest = pd.Timestamp("2026-09-24 03:45", tz="UTC")
    assert decide_candle(None, newest) == "skip_initial"
    assert decide_candle(newest, newest + pd.Timedelta(minutes=15)) == "evaluate"
    assert f"{newest.strftime('%H:%M')} UTC" == "03:45 UTC"


# ------------------------------------------------------------------ cierre de posicion

def test_closing_side_is_the_opposite_of_the_position() -> None:
    assert closing_side("long") == "Sell"
    assert closing_side("short") == "Buy"


# ------------------------------------------------------------------ comisiones y funding reales

def exec_(exec_type: str, fee: float) -> dict:
    return {"exec_type": exec_type, "fee": fee}


def test_trade_fees_sum_only_trade_executions_as_a_cost() -> None:
    costs = summarize_trade_costs([exec_("Trade", 0.055), exec_("Trade", 0.02), exec_("Funding", 0.01)])
    assert costs.fees == pytest.approx(0.075)  # solo las dos ejecuciones de tipo Trade


def test_maker_rebate_is_a_negative_fee_and_reduces_the_total() -> None:
    costs = summarize_trade_costs([exec_("Trade", 0.055), exec_("Trade", -0.01)])  # salida con rebate maker
    assert costs.fees == pytest.approx(0.045)


def test_funding_sign_is_flipped_from_bybits_fee_convention_to_match_the_backtest() -> None:
    """Bybit informa el funding como 'fee' (positivo = cargo a la cuenta). El backtest usa la convencion opuesta
    (positivo = se cobro). Pagar funding (fee positivo en Bybit) debe dar funding NEGATIVO en el resultado."""
    paid = summarize_trade_costs([exec_("Funding", 0.007)])
    assert paid.funding == pytest.approx(-0.007) and paid.fees == 0.0

    received = summarize_trade_costs([exec_("Funding", -0.001)])
    assert received.funding == pytest.approx(0.001)


def test_multiple_funding_settlements_are_summed() -> None:
    costs = summarize_trade_costs([exec_("Funding", 0.007), exec_("Funding", -0.001), exec_("Funding", 0.003)])
    assert costs.funding == pytest.approx(-(0.007 - 0.001 + 0.003))


def test_no_executions_means_zero_cost_not_none() -> None:
    costs = summarize_trade_costs([])
    assert costs.fees == 0.0 and costs.funding == 0.0
