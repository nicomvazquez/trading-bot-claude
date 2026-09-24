import numpy as np
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.indicators import atr_series
from app.strategies.registry import register


class DonchianParams(BaseModel):
    entry_bars: int = Field(default=20, ge=5, le=300, description="Velas del canal de entrada (máximo/mínimo a romper)")
    exit_bars: int = Field(default=10, ge=2, le=200, description="Velas del canal de salida (más corto que el de entrada)")
    atr_period: int = Field(default=14, ge=2, le=100, description="Período del ATR")
    vol_lookback: int = Field(default=50, ge=10, le=500, description="Velas para promediar el ATR de referencia")
    min_vol_ratio: float = Field(default=1.0, ge=0.0, le=5.0, description="ATR actual / ATR promedio mínimo (0 = sin filtro de volatilidad)")
    stop_atr_mult: float = Field(default=2.0, ge=0.5, le=10.0, description="Distancia del stop en múltiplos de ATR")
    allow_long: bool = Field(default=True, description="Permitir largos")
    allow_short: bool = Field(default=True, description="Permitir cortos")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


@register
class DonchianBreakoutStrategy(Strategy):
    """Ruptura de canal de Donchian (estilo Turtle) con filtro de volatilidad.

    Entrada: el cierre supera el máximo (o rompe el mínimo) de las últimas `entry_bars` velas, y el ATR actual
    está al menos en `min_vol_ratio` veces su promedio (la ruptura ocurre con volatilidad en expansión, no en un
    mercado dormido). Salida: el cierre rompe el canal de `exit_bars` velas en contra, o el stop de `stop_atr_mult`
    ATR. No usa take profit: deja correr las ganancias."""

    key = "donchian_breakout"
    display_name = "Ruptura de canal (Donchian)"
    description = (
        "Seguidor de tendencia clásico: entra cuando el precio rompe el máximo o mínimo de las últimas N velas, "
        "solo si la volatilidad está en expansión. Sale al romper el canal corto en contra o por stop en ATR. "
        "Gana en tendencias fuertes y pierde pequeñas cantidades repetidas en mercados laterales."
    )
    params_model = DonchianParams

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        p: DonchianParams = self.params
        need = max(p.entry_bars, p.exit_bars, p.atr_period + p.vol_lookback) + 2
        if len(ctx.candles) < need:
            return None
        w = ctx.candles.iloc[-need:]
        high, low, close = (w[c].to_numpy(dtype=float) for c in ("high", "low", "close"))
        price = float(close[-1])

        if ctx.position is not None:
            exit_low = low[-(p.exit_bars + 1):-1].min()
            exit_high = high[-(p.exit_bars + 1):-1].max()
            if ctx.position.side == "long" and price < exit_low:
                return Signal(action="close", reason=f"rompió el mínimo de {p.exit_bars} velas")
            if ctx.position.side == "short" and price > exit_high:
                return Signal(action="close", reason=f"rompió el máximo de {p.exit_bars} velas")
            return None

        atr = atr_series(high, low, close, p.atr_period)
        if len(atr) < p.vol_lookback:
            return None
        atr_now, atr_ref = float(atr[-1]), float(atr[-p.vol_lookback:].mean())
        if atr_now <= 0 or atr_ref <= 0 or atr_now / atr_ref < p.min_vol_ratio:
            return None

        upper = high[-(p.entry_bars + 1):-1].max()
        lower = low[-(p.entry_bars + 1):-1].min()
        stop_dist = atr_now * p.stop_atr_mult
        if p.allow_long and price > upper:
            return Signal(action="buy", reason=f"ruptura del máximo de {p.entry_bars} velas (ATR x{atr_now / atr_ref:.2f})",
                          stop_loss=price - stop_dist, risk_pct=p.risk_pct)
        if p.allow_short and price < lower:
            return Signal(action="sell", reason=f"ruptura del mínimo de {p.entry_bars} velas (ATR x{atr_now / atr_ref:.2f})",
                          stop_loss=price + stop_dist, risk_pct=p.risk_pct)
        return None
