import numpy as np
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.indicators import atr_series, rolling_mean_std
from app.strategies.registry import register


class SqueezeParams(BaseModel):
    bb_period: int = Field(default=20, ge=5, le=200, description="Período de las bandas de Bollinger")
    bb_std: float = Field(default=2.0, ge=0.5, le=4.0, description="Desvíos estándar de las bandas")
    lookback: int = Field(default=120, ge=30, le=1000, description="Velas para medir qué tan angostas están las bandas (percentil)")
    squeeze_pct: float = Field(default=20, ge=1, le=50, description="Percentil de ancho de banda por debajo del cual hay compresión")
    squeeze_recent_bars: int = Field(default=6, ge=1, le=50, description="La compresión debe haber ocurrido en las últimas N velas")
    atr_period: int = Field(default=14, ge=2, le=100, description="Período del ATR (para el stop)")
    stop_atr_mult: float = Field(default=2.0, ge=0.5, le=10.0, description="Distancia del stop en múltiplos de ATR")
    allow_long: bool = Field(default=True, description="Permitir largos")
    allow_short: bool = Field(default=True, description="Permitir cortos")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


@register
class VolatilitySqueezeStrategy(Strategy):
    """Compresión y expansión de volatilidad.

    1. Compresión: el ancho de las bandas de Bollinger cae por debajo de su percentil `squeeze_pct` de las
       últimas `lookback` velas (el mercado se duerme y acumula energía).
    2. Expansión: dentro de las siguientes `squeeze_recent_bars` velas, el cierre sale de la banda superior
       (largo) o inferior (corto).
    3. Salida: el cierre vuelve a cruzar la media central, o el stop de `stop_atr_mult` ATR."""

    key = "volatility_squeeze"
    display_name = "Compresión y expansión de volatilidad"
    description = (
        "Detecta cuando las bandas de Bollinger se estrechan (mercado dormido) y opera la ruptura cuando el precio "
        "sale de la banda. Sale al volver a la media o por stop en ATR. Pocas operaciones, ligadas a arranques de "
        "movimiento."
    )
    params_model = SqueezeParams

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        p: SqueezeParams = self.params
        need = max(p.bb_period + p.lookback, p.atr_period + 2) + 1
        if len(ctx.candles) < need:
            return None
        w = ctx.candles.iloc[-need:]
        high, low, close = (w[c].to_numpy(dtype=float) for c in ("high", "low", "close"))
        price = float(close[-1])
        mid, std = rolling_mean_std(close, p.bb_period)

        if ctx.position is not None:
            if ctx.position.side == "long" and price < mid[-1]:
                return Signal(action="close", reason="volvió a la media (banda central)")
            if ctx.position.side == "short" and price > mid[-1]:
                return Signal(action="close", reason="volvió a la media (banda central)")
            return None

        width = 2 * p.bb_std * std / np.where(mid == 0, np.nan, mid)
        history = width[-p.lookback:]
        if np.isnan(history).any():
            return None
        threshold = np.percentile(history, p.squeeze_pct)
        recent = width[-(p.squeeze_recent_bars + 1):-1]  # las velas anteriores a la actual
        if not (recent <= threshold).any():
            return None

        atr = atr_series(high, low, close, p.atr_period)
        if len(atr) == 0 or atr[-1] <= 0:
            return None
        stop_dist = float(atr[-1]) * p.stop_atr_mult
        upper, lower = mid[-1] + p.bb_std * std[-1], mid[-1] - p.bb_std * std[-1]
        if p.allow_long and price > upper:
            return Signal(action="buy", reason="expansión al alza tras compresión", stop_loss=price - stop_dist, risk_pct=p.risk_pct)
        if p.allow_short and price < lower:
            return Signal(action="sell", reason="expansión a la baja tras compresión", stop_loss=price + stop_dist, risk_pct=p.risk_pct)
        return None
