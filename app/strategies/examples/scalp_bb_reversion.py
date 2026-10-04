import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.indicators import atr_series, rolling_mean_std, rsi_wilder
from app.strategies.registry import register


class ScalpBbParams(BaseModel):
    bb_period: int = Field(default=20, ge=5, le=100, description="Período de las bandas de Bollinger (velas)")
    bb_std: float = Field(default=2.0, ge=0.5, le=4.0, description="Desvío estándar de las bandas")
    rsi_period: int = Field(default=14, ge=2, le=50, description="Período del RSI")
    rsi_oversold: float = Field(default=25.0, ge=1.0, le=50.0, description="RSI por debajo de este valor habilita un largo")
    rsi_overbought: float = Field(default=75.0, ge=50.0, le=99.0, description="RSI por encima de este valor habilita un corto")
    atr_period: int = Field(default=14, ge=2, le=100, description="Período del ATR para el stop")
    stop_atr_mult: float = Field(default=1.5, ge=0.2, le=10.0, description="Stop a N × ATR de la entrada")
    min_target_pct: float = Field(default=0.25, ge=0.0, le=5.0, description="Distancia mínima a la media para operar (% del precio); tiene que superar los costos")
    min_reward_risk: float = Field(default=1.0, ge=0.0, le=5.0, description="Distancia a la media mínima como múltiplo del stop (1 = el objetivo está al menos tan lejos como el riesgo)")
    use_trend_filter: bool = Field(default=False, description="No revertir contra una tendencia clara del timeframe superior")
    trend_minutes: int = Field(default=60, ge=15, le=1440, description="Timeframe superior del filtro de tendencia, en minutos")
    trend_ema_period: int = Field(default=20, ge=5, le=200, description="Período de la EMA del timeframe superior")
    allow_long: bool = Field(default=True, description="Permitir operaciones largas")
    allow_short: bool = Field(default=True, description="Permitir operaciones cortas")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


@register
class ScalpBbReversionStrategy(Strategy):
    """Scalping de reversión a la media: el precio sale de las bandas de Bollinger con RSI extremo y
    se espera que vuelva a la media. Largo: cierre bajo la banda inferior y RSI sobrevendido; objetivo
    en la media, stop a N × ATR. El corto es el espejo. Solo usa velas cerradas."""

    key = "scalp_bb_reversion"
    display_name = "Scalping: reversión a la media (Bollinger + RSI)"
    description = (
        "Entra cuando el precio cierra fuera de las bandas de Bollinger con el RSI en zona extrema y apunta a la "
        "media móvil. Stop por ATR. Pensada para velas de 5 minutos con activos de rango amplio: opera mucho, así "
        "que el movimiento por operación tiene que superar los costos (min_target_pct)."
    )
    params_model = ScalpBbParams
    style = "Reversión · scalping"
    default_timeframe = "5"

    def __init__(self, params: BaseModel) -> None:
        super().__init__(params)
        self._trend_cache: dict[int, int] = {}

    def _trend(self, candles: pd.DataFrame, base: pd.Timedelta) -> int:
        """+1 si la EMA del timeframe superior sube y el cierre está por encima; -1 si baja y está por debajo;
        0 si no hay tendencia clara. Usa solo velas superiores completas."""
        p: ScalpBbParams = self.params
        htf = pd.Timedelta(minutes=p.trend_minutes)
        last_open = candles.index[-1]
        bar_start = last_open.floor(htf)
        boundary = bar_start + htf if last_open + base >= bar_start + htf else bar_start
        key = int(boundary.value)
        if key in self._trend_cache:
            return self._trend_cache[key]
        window = candles.loc[(candles.index >= boundary - htf * (p.trend_ema_period * 4)) & (candles.index < boundary), "close"]
        closes = window.resample(htf, label="left", closed="left").last().dropna()
        trend = 0
        if len(closes) > p.trend_ema_period + 1:
            ema = closes.ewm(span=p.trend_ema_period, adjust=False).mean()
            if closes.iloc[-1] > ema.iloc[-1] and ema.iloc[-1] > ema.iloc[-2]:
                trend = 1
            elif closes.iloc[-1] < ema.iloc[-1] and ema.iloc[-1] < ema.iloc[-2]:
                trend = -1
        if len(self._trend_cache) > 5000:
            self._trend_cache.clear()
        self._trend_cache[key] = trend
        return trend

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        if ctx.position is not None:
            return None
        p: ScalpBbParams = self.params
        warmup = max(p.bb_period, p.rsi_period * 6, p.atr_period) + 5
        if len(ctx.candles) < warmup:
            return None
        w = ctx.candles.iloc[-(warmup + 200):]
        close, high, low = (w[c].to_numpy(dtype=float) for c in ("close", "high", "low"))

        mean, std = rolling_mean_std(close, p.bb_period)
        rsi = rsi_wilder(close, p.rsi_period)
        atr = atr_series(high, low, close, p.atr_period)
        if len(mean) == 0 or len(rsi) == 0 or len(atr) == 0 or atr[-1] <= 0:
            return None

        price = float(close[-1])
        trend = 0
        if p.use_trend_filter:
            base = pd.Series(ctx.candles.index[-5:]).diff().median()
            if pd.isna(base) or base <= pd.Timedelta(0):
                return None
            trend = self._trend(ctx.candles, base)
        middle = float(mean[-1])
        upper = middle + p.bb_std * float(std[-1])
        lower = middle - p.bb_std * float(std[-1])
        stop_dist = float(atr[-1]) * p.stop_atr_mult

        if p.allow_long and trend >= 0 and price < lower and rsi[-1] < p.rsi_oversold:
            distance_pct = (middle - price) / price * 100
            if distance_pct >= p.min_target_pct and (middle - price) >= p.min_reward_risk * stop_dist:
                return Signal(
                    action="buy",
                    reason=f"cierre bajo la banda inferior, RSI {rsi[-1]:.0f}; objetivo en la media {middle:.2f}",
                    stop_loss=price - stop_dist, take_profit=middle, risk_pct=p.risk_pct,
                )
        if p.allow_short and trend <= 0 and price > upper and rsi[-1] > p.rsi_overbought:
            distance_pct = (price - middle) / price * 100
            if distance_pct >= p.min_target_pct and (price - middle) >= p.min_reward_risk * stop_dist:
                return Signal(
                    action="sell",
                    reason=f"cierre sobre la banda superior, RSI {rsi[-1]:.0f}; objetivo en la media {middle:.2f}",
                    stop_loss=price + stop_dist, take_profit=middle, risk_pct=p.risk_pct,
                )
        return None
