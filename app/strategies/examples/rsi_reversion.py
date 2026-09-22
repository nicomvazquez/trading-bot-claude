import pandas as pd
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.registry import register


def _rsi(closes: pd.Series, period: int) -> pd.Series:
    delta = closes.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.rolling(window=period).mean()
    avg_loss = losses.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


class RsiReversionParams(BaseModel):
    rsi_period: int = Field(default=14, ge=2, le=100, description="Periodo del RSI")
    oversold: float = Field(default=30, ge=1, le=49, description="RSI por debajo del cual compra (sobreventa)")
    overbought: float = Field(default=70, ge=51, le=99, description="RSI por encima del cual cierra (sobrecompra)")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operacion")
    stop_loss_pct: float = Field(default=3.0, ge=0.1, le=20.0, description="% de stop-loss respecto al precio de entrada")


@register
class RsiReversionStrategy(Strategy):
    key = "rsi_reversion"
    display_name = "Reversion a la media (RSI)"
    params_model = RsiReversionParams

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        params: RsiReversionParams = self.params
        closes = ctx.candles["close"]
        if len(closes) < params.rsi_period + 2:
            return None

        rsi = _rsi(closes, params.rsi_period)
        current_rsi = rsi.iloc[-1]
        price = closes.iloc[-1]

        if ctx.position is None and current_rsi < params.oversold:
            return Signal(
                action="buy",
                reason=f"RSI {current_rsi:.1f} < {params.oversold} (sobreventa)",
                stop_loss=price * (1 - params.stop_loss_pct / 100),
                risk_pct=params.risk_pct,
            )

        if ctx.position is not None and ctx.position.side == "long" and current_rsi > params.overbought:
            return Signal(action="close", reason=f"RSI {current_rsi:.1f} > {params.overbought} (sobrecompra)")

        return None
