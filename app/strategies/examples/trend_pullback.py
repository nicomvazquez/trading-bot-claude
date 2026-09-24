import pandas as pd
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.indicators import atr_series, rsi_wilder
from app.strategies.registry import register


class TrendPullbackParams(BaseModel):
    htf_minutes: int = Field(default=240, ge=30, le=1440, description="Timeframe superior de la tendencia, en minutos (240 = 4h)")
    htf_ema_period: int = Field(default=50, ge=10, le=200, description="Período de la EMA de tendencia en el timeframe superior")
    rsi_period: int = Field(default=14, ge=2, le=50, description="Período del RSI de entrada")
    rsi_pullback: float = Field(default=40, ge=10, le=49, description="Nivel de RSI del retroceso (largo: cruza hacia arriba este nivel; corto: el simétrico)")
    atr_period: int = Field(default=14, ge=2, le=100, description="Período del ATR (para el stop)")
    stop_atr_mult: float = Field(default=1.5, ge=0.5, le=10.0, description="Distancia del stop en múltiplos de ATR")
    rr_ratio: float = Field(default=2.0, ge=0.5, le=10.0, description="Take profit como múltiplo del riesgo (R)")
    exit_on_trend_flip: bool = Field(default=True, description="Cerrar si la tendencia del timeframe superior se da vuelta")
    allow_long: bool = Field(default=True, description="Permitir largos")
    allow_short: bool = Field(default=True, description="Permitir cortos")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


@register
class TrendPullbackStrategy(Strategy):
    """Retroceso a favor de la tendencia, en dos timeframes.

    Tendencia (timeframe superior, por defecto 4h): el cierre está por encima (alcista) o por debajo (bajista) de
    su EMA. Se usan SOLO velas superiores ya cerradas: la vela de 4h en formación nunca cuenta.
    Entrada (timeframe de la instancia): en tendencia alcista, el RSI venía por debajo de `rsi_pullback` y
    vuelve a cruzarlo hacia arriba (retroceso terminado). En bajista, el simétrico.
    Salida: stop en ATR, take profit en múltiplo del riesgo, o giro de la tendencia superior."""

    key = "trend_pullback"
    display_name = "Retroceso a favor de la tendencia (multi-timeframe)"
    description = (
        "Define la tendencia en 4 horas y entra en el timeframe menor cuando el RSI termina un retroceso y "
        "retoma la dirección de la tendencia. Stop en ATR y take profit en múltiplo del riesgo. Pensada para "
        "timeframes de 15 minutos a 1 hora."
    )
    params_model = TrendPullbackParams

    def __init__(self, params: BaseModel) -> None:
        super().__init__(params)
        self._trend_cache: dict[int, int] = {}

    # ------------------------------------------------------------ tendencia superior

    def _trend(self, candles: pd.DataFrame, base: pd.Timedelta) -> int:
        """+1 alcista, -1 bajista, 0 sin dato. Usa solo velas superiores completas y cachea por vela superior."""
        p: TrendPullbackParams = self.params
        htf = pd.Timedelta(minutes=p.htf_minutes)
        last_open = candles.index[-1]
        bar_start = last_open.floor(htf)
        # la vela superior que contiene a la actual esta completa solo si la actual es su ultima subvela
        boundary = bar_start + htf if last_open + base >= bar_start + htf else bar_start
        key = int(boundary.value)
        if key in self._trend_cache:
            return self._trend_cache[key]

        window_start = boundary - htf * (p.htf_ema_period * 3)
        sub = candles.loc[(candles.index >= window_start) & (candles.index < boundary), "close"]
        closes = sub.resample(htf, label="left", closed="left").last().dropna()
        trend = 0
        if len(closes) >= p.htf_ema_period:
            ema = closes.ewm(span=p.htf_ema_period, adjust=False).mean()
            trend = 1 if closes.iloc[-1] > ema.iloc[-1] else -1
        if len(self._trend_cache) > 5000:
            self._trend_cache.clear()
        self._trend_cache[key] = trend
        return trend

    # ------------------------------------------------------------ senal

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        p: TrendPullbackParams = self.params
        candles = ctx.candles
        if len(candles) < 5:
            return None
        base = pd.Series(candles.index[-5:]).diff().median()
        if pd.isna(base) or base >= pd.Timedelta(minutes=p.htf_minutes) or pd.Timedelta(minutes=p.htf_minutes) % base != pd.Timedelta(0):
            return None  # el timeframe de la instancia debe ser menor que el superior y dividirlo exacto

        trend = self._trend(candles, base)
        if trend == 0:
            return None

        if ctx.position is not None:
            if p.exit_on_trend_flip and ((ctx.position.side == "long" and trend < 0) or (ctx.position.side == "short" and trend > 0)):
                return Signal(action="close", reason="la tendencia del timeframe superior se dio vuelta")
            return None

        warmup = max(p.rsi_period * 6, p.atr_period + 2, 60)
        if len(candles) < warmup:
            return None
        w = candles.iloc[-warmup:]
        close, high, low = (w[c].to_numpy(dtype=float) for c in ("close", "high", "low"))
        rsi = rsi_wilder(close, p.rsi_period)
        atr = atr_series(high, low, close, p.atr_period)
        if len(rsi) < 2 or len(atr) == 0 or atr[-1] <= 0:
            return None

        price = float(close[-1])
        stop_dist = float(atr[-1]) * p.stop_atr_mult
        level = p.rsi_pullback
        if trend > 0 and p.allow_long and rsi[-2] < level <= rsi[-1]:
            return Signal(action="buy", reason=f"retroceso terminado en tendencia alcista (RSI {rsi[-1]:.0f})",
                          stop_loss=price - stop_dist, take_profit=price + stop_dist * p.rr_ratio, risk_pct=p.risk_pct)
        if trend < 0 and p.allow_short and rsi[-2] > 100 - level >= rsi[-1]:
            return Signal(action="sell", reason=f"retroceso terminado en tendencia bajista (RSI {rsi[-1]:.0f})",
                          stop_loss=price + stop_dist, take_profit=price - stop_dist * p.rr_ratio, risk_pct=p.risk_pct)
        return None
