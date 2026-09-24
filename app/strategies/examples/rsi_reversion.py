import numpy as np
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


def _rsi_last(closes: np.ndarray, period: int) -> float:
    """Ultimo valor del RSI (media simple) usando solo las ultimas period + 1 velas.
    Equivale a `_rsi(...).iloc[-1]` pero sin el costo de pandas en cada vela."""
    diff = np.diff(closes[-(period + 1):])
    avg_gain = float(np.maximum(diff, 0.0).mean())
    avg_loss = float(np.maximum(-diff, 0.0).mean())
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else float("nan")
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


class RsiReversionParams(BaseModel):
    rsi_period: int = Field(default=14, ge=2, le=100, description="Período del RSI")
    oversold: float = Field(default=30, ge=1, le=49, description="RSI por debajo del cual compra (sobreventa)")
    overbought: float = Field(default=70, ge=51, le=99, description="RSI por encima del cual cierra (sobrecompra)")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")
    stop_loss_pct: float = Field(default=3.0, ge=0.1, le=20.0, description="% de stop-loss respecto al precio de entrada")


@register
class RsiReversionStrategy(Strategy):
    key = "rsi_reversion"
    display_name = "Reversión a la media (RSI)"
    description = (
        "Compra cuando el RSI cae por debajo del nivel de sobreventa (espera un rebote) y cierra cuando llega a "
        "sobrecompra. Opera solo largos, con stop-loss fijo. Funciona mejor en mercados laterales."
    )
    params_model = RsiReversionParams

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        params: RsiReversionParams = self.params
        # el RSI (media simple) solo depende de las ultimas rsi_period + 1 velas:
        # recalcular sobre todo el historial en cada vela es costo cuadratico sin cambiar el resultado
        closes = ctx.candles["close"].iloc[-(params.rsi_period + 5):].to_numpy(dtype=float)
        if len(closes) < params.rsi_period + 2:
            return None

        current_rsi = _rsi_last(closes, params.rsi_period)
        price = float(closes[-1])

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
