import datetime as dt

import pytest

from app.tools.demo_data import FEE_RATE, SPECS, NotADemoDatabase, assert_demo_database, generate

NOW = dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc)


def test_seeding_is_refused_on_anything_but_a_demo_database() -> None:
    for name in ("trading_bot", "trading_demo_backup", "demo", "postgres"):
        with pytest.raises(NotADemoDatabase):
            assert_demo_database(name)
    assert_demo_database("trading_demo")  # no lanza


def test_generation_is_deterministic() -> None:
    assert generate(NOW, seed=3)["trades"] == generate(NOW, seed=3)["trades"]
    assert generate(NOW, seed=3)["trades"] != generate(NOW, seed=4)["trades"]


def test_every_closed_trade_pnl_matches_price_qty_and_fees() -> None:
    for t in (t for t in generate(NOW)["trades"] if t["closed_at"]):
        direction = 1 if t["side"] == "long" else -1
        fees = (t["entry_price"] + t["exit_price"]) * t["qty"] * FEE_RATE
        assert t["pnl"] == pytest.approx(direction * (t["exit_price"] - t["entry_price"]) * t["qty"] - fees, abs=1e-3)
        assert t["closed_at"] > t["opened_at"]


def test_an_instance_never_has_overlapping_trades_and_at_most_one_open() -> None:
    data = generate(NOW)
    for spec in SPECS:
        mine = sorted((t for t in data["trades"] if t["instance"] == spec.name), key=lambda t: t["opened_at"])
        for a, b in zip(mine, mine[1:], strict=False):
            assert a["closed_at"] is not None and a["closed_at"] <= b["opened_at"], f"{spec.name}: trades superpuestos"
        assert sum(1 for t in mine if t["closed_at"] is None) <= 1


def test_nothing_is_in_the_future_and_open_trades_have_no_result() -> None:
    data = generate(NOW)
    for t in data["trades"]:
        assert t["opened_at"] <= NOW and (t["closed_at"] is None or t["closed_at"] <= NOW)
        if t["closed_at"] is None:
            assert t["pnl"] is None and t["exit_price"] is None and t["exit_reason"] is None
    assert all(e["timestamp"] <= NOW for e in data["events"])


def test_orders_reconcile_with_trades() -> None:
    data = generate(NOW)
    closed = sum(1 for t in data["trades"] if t["closed_at"])
    open_ = len(data["trades"]) - closed
    assert len(data["orders"]) == closed * 2 + open_
    assert len({o["exchange_order_id"] for o in data["orders"]}) == len(data["orders"])


def test_the_dataset_shows_variety_in_every_panel() -> None:
    data = generate(NOW)
    trades = [t for t in data["trades"] if t["closed_at"]]
    assert len(trades) > 100
    assert {t["side"] for t in trades} == {"long", "short"}
    assert any(t["pnl"] > 0 for t in trades) and any(t["pnl"] < 0 for t in trades)
    assert len({t["exit_reason"] for t in trades}) >= 3
    assert sum(1 for t in data["trades"] if t["closed_at"] is None) == 2          # posiciones abiertas para el Resumen
    assert {e["kind"] for e in data["events"]} >= {"started", "opened", "closed", "rejected", "error"}
    assert len(data["instances"]) == len(SPECS) and len(data["runs"]) >= 5
    assert not any(i["is_active"] for i in data["instances"])                      # nunca activas: no arrancan runners


def test_parameters_are_the_real_defaults_of_each_strategy() -> None:
    from app.strategies import registry

    for inst in generate(NOW)["instances"]:
        assert inst["params"] == registry.get(inst["strategy_key"]).params_model().model_dump()
