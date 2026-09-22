from dataclasses import dataclass

from app.risk.sizing import position_size
from app.strategies.base import Signal


@dataclass
class RiskCheck:
    approved: bool
    qty: float = 0.0
    reason: str = ""


@dataclass
class RiskLimits:
    kill_switch: bool = False
    max_daily_loss_pct: float = 5.0
    max_concurrent_positions: int = 5
    max_leverage: float = 10.0


class RiskManager:
    """Filtra y dimensiona las senales de las estrategias antes de que
    lleguen al exchange. Es el unico camino por el que una estrategia puede
    terminar generando una orden real: nunca se salta este chequeo."""

    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def evaluate_entry(
        self,
        signal: Signal,
        equity: float,
        price: float,
        daily_pnl_pct: float,
        open_positions_count: int,
    ) -> RiskCheck:
        if self.limits.kill_switch:
            return RiskCheck(approved=False, reason="kill-switch activado")

        if daily_pnl_pct <= -abs(self.limits.max_daily_loss_pct):
            return RiskCheck(
                approved=False,
                reason=f"perdida diaria {daily_pnl_pct:.2f}% supera el limite de "
                f"{self.limits.max_daily_loss_pct:.2f}%",
            )

        if open_positions_count >= self.limits.max_concurrent_positions:
            return RiskCheck(
                approved=False,
                reason=f"ya hay {open_positions_count} posiciones abiertas "
                f"(limite {self.limits.max_concurrent_positions})",
            )

        if equity <= 0:
            return RiskCheck(approved=False, reason="equity de la instancia es cero o negativo")

        qty = position_size(equity, price, signal.stop_loss, signal.risk_pct, self.limits.max_leverage)
        if qty <= 0:
            return RiskCheck(approved=False, reason="el tamano calculado de la operacion es cero")

        return RiskCheck(approved=True, qty=qty)
