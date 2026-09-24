import numpy as np
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.registry import register


class SmaCrossParams(BaseModel):
    fast_period: int = Field(default=10, ge=2, le=200, description="Período de la SMA rápida")
    slow_period: int = Field(default=50, ge=5, le=400, description="Período de la SMA lenta")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")
    stop_loss_pct: float = Field(default=2.0, ge=0.1, le=20.0, description="% de stop-loss respecto al precio de entrada")


@register
class SmaCrossStrategy(Strategy):
    key = "sma_cross"
    display_name = "Cruce de Medias Móviles (SMA)"
    description = (
        "Va largo cuando la media móvil rápida cruza hacia arriba a la lenta, y corto cuando cruza hacia abajo. "
        "Sigue tendencias: gana en mercados direccionales y pierde en laterales."
    )
    params_model = SmaCrossParams

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        params: SmaCrossParams = self.params
        # las SMA solo dependen de las ultimas slow_period + 1 velas (evita recalcular todo el historial)
        closes = ctx.candles["close"].iloc[-(params.slow_period + 5):].to_numpy(dtype=float)
        if len(closes) < params.slow_period + 2:
            return None

        fast, slow = params.fast_period, params.slow_period
        prev_diff = closes[-(fast + 1):-1].mean() - closes[-(slow + 1):-1].mean()
        curr_diff = closes[-fast:].mean() - closes[-slow:].mean()
        price = float(closes[-1])

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
