from app.risk.manager import RiskLimits, RiskManager
from app.risk.sizing import position_size
from app.strategies.base import Signal


def test_position_size_scales_with_risk_and_stop_distance() -> None:
    qty = position_size(equity=1000.0, price=100.0, stop_loss=95.0, risk_pct=1.0, max_leverage=10.0)
    assert abs(qty - (10.0 / 5.0)) < 1e-9  # arriesga 10 usd en 5 usd de distancia = 2 unidades


def test_position_size_capped_by_leverage() -> None:
    qty = position_size(equity=1000.0, price=100.0, stop_loss=99.99, risk_pct=1.0, max_leverage=5.0)
    assert qty * 100.0 <= 1000.0 * 5.0 + 1e-6


def test_kill_switch_blocks_every_entry() -> None:
    rm = RiskManager(RiskLimits(kill_switch=True))
    result = rm.evaluate_entry(Signal(action="buy", stop_loss=95.0), equity=1000.0, price=100.0, daily_pnl_pct=0.0, open_positions_count=0)
    assert not result.approved
    assert "kill-switch" in result.reason


def test_daily_loss_limit_blocks_entry() -> None:
    rm = RiskManager(RiskLimits(max_daily_loss_pct=5.0))
    result = rm.evaluate_entry(Signal(action="buy", stop_loss=95.0), equity=1000.0, price=100.0, daily_pnl_pct=-6.0, open_positions_count=0)
    assert not result.approved
    assert "perdida diaria" in result.reason


def test_daily_loss_within_limit_is_allowed() -> None:
    rm = RiskManager(RiskLimits(max_daily_loss_pct=5.0))
    result = rm.evaluate_entry(Signal(action="buy", stop_loss=95.0), equity=1000.0, price=100.0, daily_pnl_pct=-2.0, open_positions_count=0)
    assert result.approved


def test_max_concurrent_positions_blocks_entry() -> None:
    rm = RiskManager(RiskLimits(max_concurrent_positions=2))
    result = rm.evaluate_entry(Signal(action="buy", stop_loss=95.0), equity=1000.0, price=100.0, daily_pnl_pct=0.0, open_positions_count=2)
    assert not result.approved
    assert "posiciones abiertas" in result.reason


def test_approved_entry_returns_sized_qty() -> None:
    rm = RiskManager(RiskLimits())
    result = rm.evaluate_entry(Signal(action="buy", stop_loss=90.0, risk_pct=2.0), equity=1000.0, price=100.0, daily_pnl_pct=0.0, open_positions_count=0)
    assert result.approved
    assert abs(result.qty - (20.0 / 10.0)) < 1e-9
