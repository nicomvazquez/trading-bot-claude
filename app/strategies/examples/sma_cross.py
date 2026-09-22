from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.registry import register


class SmaCrossParams(BaseModel):
    fast_period: int = Field(default=10, ge=2, le=200, description="Periodo de la SMA rapida")
    slow_period: int = Field(default=50, ge=5, le=400, description="Periodo de la SMA lenta")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operacion")
    stop_loss_pct: float = Field(default=2.0, ge=0.1, le=20.0, description="% de stop-loss respecto al precio de entrada")


@register
class SmaCrossStrategy(Strategy):
    key = "sma_cross"
    display_name = "Cruce de Medias Moviles (SMA)"
    params_model = SmaCrossParams

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        params: SmaCrossParams = self.params
        closes = ctx.candles["close"]
        if len(closes) < params.slow_period + 2:
            return None

        fast = closes.rolling(window=params.fast_period).mean()
        slow = closes.rolling(window=params.slow_period).mean()

        prev_diff = fast.iloc[-2] - slow.iloc[-2]
        curr_diff = fast.iloc[-1] - slow.iloc[-1]
        price = closes.iloc[-1]

        crossed_up = prev_diff <= 0 and curr_diff > 0
        crossed_down = prev_diff >= 0 and curr_diff < 0

        if crossed_up and (ctx.position is None or ctx.position.side == "short"):
            return Signal(
                action="buy",
                reason="SMA rapida cruzo hacia arriba a la lenta",
                stop_loss=price * (1 - params.stop_loss_pct / 100),
                risk_pct=params.risk_pct,
            )

        if crossed_down and ctx.position is not None and ctx.position.side == "long":
            return Signal(action="close", reason="SMA rapida cruzo hacia abajo a la lenta")

        return None
