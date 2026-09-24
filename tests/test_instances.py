import pytest

from app.live.instances import InstanceError, _validate, evaluate_sizing

BTC = dict(price=84000.0, max_leverage=10.0, min_qty=0.001, qty_step=0.001)


def test_small_capital_cannot_reach_the_symbol_minimum() -> None:
    p = evaluate_sizing(100.0, risk_pct=1.0, stop_loss_pct=3.0, **BTC)
    assert not p.ok and p.qty == 0
    assert p.min_capital == pytest.approx(0.001 * 84000 * 3.0 / 1.0)  # 252 USD por riesgo
    assert "no podría operar" in p.message


def test_enough_capital_gives_a_rounded_position() -> None:
    p = evaluate_sizing(1000.0, risk_pct=1.0, stop_loss_pct=3.0, **BTC)
    assert p.ok
    assert p.qty == pytest.approx(0.003)  # 10 USD de riesgo / 2520 USD de distancia = 0.00396 -> paso 0.001
    assert p.risk_amount == pytest.approx(10.0)


def test_leverage_cap_sets_the_minimum_when_the_stop_is_tight() -> None:
    # stop muy chico: el riesgo alcanzaria, pero el tope de apalancamiento limita el nocional
    p = evaluate_sizing(5.0, risk_pct=5.0, stop_loss_pct=0.1, **BTC)
    assert not p.ok
    assert p.min_capital == pytest.approx(max(0.001 * 84000 * 0.1 / 5.0, 0.001 * 84000 / 10.0))


def test_the_minimum_capital_suggested_really_works() -> None:
    p = evaluate_sizing(100.0, risk_pct=1.0, stop_loss_pct=3.0, **BTC)
    again = evaluate_sizing(p.min_capital * 1.01, risk_pct=1.0, stop_loss_pct=3.0, **BTC)
    assert again.ok


def test_validation_messages_are_user_facing() -> None:
    with pytest.raises(InstanceError, match="nombre"):
        _validate("rsi_reversion", " ", "BTCUSDT", 100, {})
    with pytest.raises(InstanceError, match="capital"):
        _validate("rsi_reversion", "a", "BTCUSDT", 0, {})
    with pytest.raises(InstanceError, match="inválidos"):
        _validate("rsi_reversion", "a", "BTCUSDT", 100, {"rsi_period": 1})
    assert _validate("rsi_reversion", "a", "btcusdt", 100, {})["rsi_period"] == 14
