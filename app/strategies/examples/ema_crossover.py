import logging
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.indicators import adx_series, atr_padded, closed_bar_boundary, resample_closed_bars
from app.strategies.registry import register

logger = logging.getLogger(__name__)


class EmaCrossoverParams(BaseModel):
    context_minutes: int = Field(default=15, ge=5, le=240, description="Timeframe de contexto (tendencia de la EMA larga), en minutos; múltiplo del timeframe de ejecución")
    fast_ema_period: int = Field(default=9, ge=2, le=100, description="Período de la EMA rápida")
    slow_ema_period: int = Field(default=21, ge=3, le=200, description="Período de la EMA lenta")
    trend_ema_period: int = Field(default=200, ge=20, le=500, description="Período de la EMA de tendencia (timeframe de contexto)")
    trend_filter_enabled: bool = Field(default=True, description="Operar largos solo con precio sobre la EMA de tendencia, y cortos solo debajo")
    trend_slope_enabled: bool = Field(default=True, description="Exigir pendiente de la EMA de tendencia en la misma dirección")
    trend_slope_lookback: int = Field(default=10, ge=1, le=100, description="Velas de contexto para medir la pendiente de la EMA de tendencia")
    minimum_trend_slope: float = Field(default=0.0, ge=-5.0, le=5.0, description="Pendiente mínima de la EMA de tendencia en el lookback (%)")
    adx_filter_enabled: bool = Field(default=False, description="Operar solo con ADX por encima del mínimo (fuerza de tendencia)")
    adx_period: int = Field(default=14, ge=2, le=100, description="Período del ADX")
    adx_minimum: float = Field(default=22.0, ge=5.0, le=60.0, description="ADX mínimo para operar")
    volume_filter_enabled: bool = Field(default=False, description="Exigir volumen relativo mínimo en la vela de entrada")
    volume_period: int = Field(default=20, ge=2, le=200, description="Velas para el volumen promedio (RVOL)")
    minimum_relative_volume: float = Field(default=1.0, ge=0.0, le=10.0, description="RVOL mínimo de la vela de entrada")
    volatility_filter_enabled: bool = Field(default=False, description="Operar solo con volatilidad dentro del rango permitido")
    atr_period: int = Field(default=14, ge=2, le=100, description="Período del ATR")
    minimum_atr_ratio: float = Field(default=0.5, ge=0.0, le=10.0, description="ATR actual / ATR promedio: mínimo permitido")
    maximum_atr_ratio: float = Field(default=2.0, ge=0.0, le=10.0, description="ATR actual / ATR promedio: máximo permitido")
    next_candle_confirmation: bool = Field(default=False, description="Entrar en la vela siguiente al cruce, si mantiene la dirección (si no, entra al cierre del cruce)")
    pullback_entry_enabled: bool = Field(default=False, description="Entrar después de un retroceso a la EMA rápida dentro de la ventana, en vez de entrar en el cruce")
    max_pullback_bars: int = Field(default=12, ge=1, le=100, description="Velas de ejecución después del cruce para que llegue el retroceso")
    pullback_tolerance_pct: float = Field(default=0.1, ge=0.0, le=2.0, description="Tolerancia para tocar la EMA rápida en el retroceso (%)")
    stop_loss_atr_multiplier: float = Field(default=1.2, ge=0.2, le=10.0, description="Stop a N × ATR de la entrada")
    take_profit_rr: float = Field(default=2.0, ge=0.5, le=10.0, description="Take profit como múltiplo del riesgo (R)")
    max_bars_in_trade: int = Field(default=48, ge=1, le=1000, description="Velas de ejecución máximas dentro de una operación; luego se cierra por tiempo")
    max_trades_per_day: int = Field(default=20, ge=1, le=100, description="Máximo de operaciones por día (UTC); el valor por defecto deja operar con frecuencia de scalping")
    cooldown_after_trade_bars: int = Field(default=3, ge=0, le=500, description="Velas de ejecución de espera después de cada entrada")
    session_filter_enabled: bool = Field(default=False, description="Operar solo dentro de la ventana horaria indicada")
    session_start_hour: float = Field(default=0.0, ge=0.0, le=24.0, description="Inicio de la ventana horaria (hora local de session_timezone)")
    session_end_hour: float = Field(default=24.0, ge=0.0, le=24.0, description="Fin de la ventana horaria (hora local de session_timezone)")
    session_timezone: str = Field(default="UTC", description="Zona horaria de la ventana (por ejemplo UTC o America/Argentina/Buenos_Aires)")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


@register
class EmaCrossoverStrategy(Strategy):
    """Cruce de EMAs confirmado por tendencia, fuerza, volumen y volatilidad.

    1. Cruce: la EMA rápida cruza a la lenta en velas de ejecución cerradas (alcista para largos, bajista para cortos).
    2. Tendencia: el precio está del lado correcto de la EMA de tendencia (calculada en el timeframe de contexto
       con velas cerradas), y esa EMA tiene pendiente en la misma dirección si está activado.
    3. Filtros opcionales: ADX mínimo, volumen relativo mínimo, volatilidad (ATR) dentro del rango, ventana horaria.
    4. Confirmación: al cierre del cruce, o en la vela siguiente si lo indica el parámetro; o, con retroceso
       activado, al cierre de una vela que toca la EMA rápida y vuelve a su lado tras el cruce.
    5. Salidas: stop a N × ATR, take profit a R fijo, cierre por tiempo si la operación dura demasiado.
    6. Límites: operaciones máximas por día, y espera de `cooldown_after_trade_bars` velas después de cada entrada.

    Solo usa velas cerradas: el cruce, el ADX, el ATR y el volumen se calculan hasta la última vela cerrada."""

    key = "ema_crossover"
    display_name = "Cruce de EMAs con confirmación (tendencia, ADX, volumen, volatilidad)"
    description = (
        "Entra cuando la EMA rápida cruza a la lenta, solo si el precio está a favor de la EMA de tendencia y los filtros "
        "de fuerza, volumen y volatilidad lo permiten. Stop por ATR, objetivo en múltiplo del riesgo, cierre por tiempo."
    )
    params_model = EmaCrossoverParams
    style = "Tendencia · cruce de medias"
    default_timeframe = "5"

    def __init__(self, params: BaseModel) -> None:
        super().__init__(params)
        self._entries: list[str] = []  # día (UTC) de cada entrada
        self._last_entry: pd.Timestamp | None = None
        self._bars_in_trade = 0
        self._trend_cache: tuple[int, int] | None = None
        self.last_rejection: str | None = None

    def _reject(self, reason: str) -> None:
        self.last_rejection = reason
        logger.debug("ema_crossover: sin operación (%s)", reason)

    # ------------------------------------------------------------ señal

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        p: EmaCrossoverParams = self.params
        self.last_rejection = None
        if ctx.position is not None:
            self._bars_in_trade += 1
            if self._bars_in_trade >= p.max_bars_in_trade:
                self._bars_in_trade = 0
                return Signal(action="close", reason=f"cierre por tiempo ({p.max_bars_in_trade} velas)")
            return None
        self._bars_in_trade = 0

        candles = ctx.candles
        if len(candles) < p.trend_ema_period * 3:
            return None
        base = pd.Series(candles.index[-5:]).diff().median()
        ctx_bar = pd.Timedelta(minutes=p.context_minutes)
        if pd.isna(base) or base <= pd.Timedelta(0) or ctx_bar <= base or ctx_bar % base != pd.Timedelta(0):
            return None  # el contexto debe ser un múltiplo exacto y mayor que el timeframe de ejecución
        if p.fast_ema_period >= p.slow_ema_period:
            return None

        now = candles.index[-1]
        if p.session_filter_enabled and not self._in_session(now):
            self._reject("sesión fuera de la ventana horaria")
            return None
        day = now.floor("1D").date().isoformat()
        if sum(1 for d in self._entries if d == day) >= p.max_trades_per_day:
            self._reject("límite diario de operaciones alcanzado")
            return None
        if self._last_entry is not None and now - self._last_entry < base * p.cooldown_after_trade_bars:
            self._reject("cooldown después de una operación")
            return None

        tail = candles.iloc[-max(p.trend_ema_period * 3, 300):]
        close = tail["close"]
        fast = close.ewm(span=p.fast_ema_period, adjust=False).mean().to_numpy(float)
        slow = close.ewm(span=p.slow_ema_period, adjust=False).mean().to_numpy(float)
        k = len(close) - 1
        if k < 3:
            return None

        bull_now = fast[k - 1] <= slow[k - 1] and fast[k] > slow[k]
        bear_now = fast[k - 1] >= slow[k - 1] and fast[k] < slow[k]
        cross_bars = self._cross_bars(fast, slow, k) if p.pullback_entry_enabled else []
        if p.pullback_entry_enabled:
            if not cross_bars:
                return None
        elif p.next_candle_confirmation:
            bull_cross = fast[k - 2] <= slow[k - 2] and fast[k - 1] > slow[k - 1]
            bear_cross = fast[k - 2] >= slow[k - 2] and fast[k - 1] < slow[k - 1]
            if not (bull_cross or bear_cross):
                return None
            bull_now = bull_cross and fast[k] > slow[k]
            bear_now = bear_cross and fast[k] < slow[k]
        elif not (bull_now or bear_now):
            return None

        for direction in ("long", "short"):
            if direction == "long" and not (bull_now or (p.pullback_entry_enabled and self._pullback_ok(tail, fast, slow, cross_bars, "long"))):
                continue
            if direction == "short" and not (bear_now or (p.pullback_entry_enabled and self._pullback_ok(tail, fast, slow, cross_bars, "short"))):
                continue
            signal = self._validate_and_build(candles, tail, fast, slow, k, direction, base, now, day)
            if signal is not None:
                return signal
        return None

    # ------------------------------------------------------------ validaciones

    def _validate_and_build(self, candles, tail, fast, slow, k, direction, base, now, day) -> Signal | None:
        p: EmaCrossoverParams = self.params
        long = direction == "long"
        price = float(tail["close"].iloc[k])

        trend, slope = self._trend_state(candles, base, price)
        if p.trend_filter_enabled:
            if (long and trend <= 0) or (not long and trend >= 0):
                self._reject("precio del otro lado de la EMA de tendencia")
                return None
        if p.trend_slope_enabled and p.trend_filter_enabled:
            if (long and slope < p.minimum_trend_slope) or (not long and slope > -p.minimum_trend_slope):
                self._reject("pendiente de la EMA de tendencia insuficiente")
                return None

        highs, lows, closes = (tail[c].to_numpy(float) for c in ("high", "low", "close"))
        if p.adx_filter_enabled:
            adx = float(adx_series(tail["high"], tail["low"], tail["close"], p.adx_period).iloc[-1])
            if np.isnan(adx) or adx < p.adx_minimum:
                self._reject(f"ADX {adx:.1f} por debajo de {p.adx_minimum}")
                return None

        if p.volume_filter_enabled:
            vols = tail["volume"].to_numpy(float)
            prev = vols[k - p.volume_period:k] if k >= p.volume_period else np.array([])
            if len(prev) < p.volume_period or prev.mean() <= 0:
                self._reject("volumen histórico insuficiente")
                return None
            rvol = vols[k] / prev.mean()
            if rvol < p.minimum_relative_volume:
                self._reject(f"volumen relativo {rvol:.2f} insuficiente")
                return None

        atr = atr_padded(highs, lows, closes, p.atr_period)
        if np.isnan(atr[k]) or atr[k] <= 0:
            self._reject("ATR no disponible")
            return None
        if p.volatility_filter_enabled:
            window = atr[max(0, k - 4 * p.atr_period + 1):k + 1]
            window = window[~np.isnan(window)]
            ratio = atr[k] / window.mean() if len(window) and window.mean() > 0 else np.nan
            if np.isnan(ratio) or not (p.minimum_atr_ratio <= ratio <= p.maximum_atr_ratio):
                self._reject(f"volatilidad fuera de rango (ATR ratio {ratio:.2f})" if not np.isnan(ratio) else "volatilidad sin dato")
                return None

        stop_dist = float(atr[k]) * p.stop_loss_atr_multiplier
        stop = price - stop_dist if long else price + stop_dist
        risk = price - stop if long else stop - price
        if risk <= 0:
            self._reject("riesgo no positivo")
            return None
        target = price + p.take_profit_rr * risk if long else price - p.take_profit_rr * risk

        self._entries.append(day)
        self._last_entry = now
        side = "alcista" if long else "bajista"
        return Signal(
            action="buy" if long else "sell",
            reason=f"cruce EMA {p.fast_ema_period}/{p.slow_ema_period} {side} · tendencia {'+' if trend > 0 else '-' if trend < 0 else '0'}",
            stop_loss=stop,
            take_profit=target,
            risk_pct=p.risk_pct,
        )

    # ------------------------------------------------------------ helpers

    def _in_session(self, ts: pd.Timestamp) -> bool:
        p: EmaCrossoverParams = self.params
        local = ts.tz_convert(ZoneInfo(p.session_timezone)) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(ZoneInfo(p.session_timezone))
        hour = local.hour + local.minute / 60
        return p.session_start_hour <= hour < p.session_end_hour

    def _cross_bars(self, fast: np.ndarray, slow: np.ndarray, k: int) -> list[tuple[int, str]]:
        p: EmaCrossoverParams = self.params
        out = []
        for j in range(max(1, k - p.max_pullback_bars), k):
            if fast[j - 1] <= slow[j - 1] and fast[j] > slow[j]:
                out.append((j, "long"))
            elif fast[j - 1] >= slow[j - 1] and fast[j] < slow[j]:
                out.append((j, "short"))
        return out

    def _pullback_ok(self, tail: pd.DataFrame, fast, slow, cross_bars, direction: str) -> bool:
        p: EmaCrossoverParams = self.params
        k = len(tail) - 1
        matching = [j for j, d in cross_bars if d == direction]
        if not matching:
            return False
        c = matching[-1]
        long = direction == "long"
        closes = tail["close"].to_numpy(float)
        highs, lows, opens = (tail[x].to_numpy(float) for x in ("high", "low", "open"))
        tol = p.pullback_tolerance_pct / 100
        touched = False
        for j in range(c + 1, k):
            if long and closes[j] < slow[j]:
                return False  # la estructura se cayó después del cruce
            if not long and closes[j] > slow[j]:
                return False
            if long and lows[j] <= fast[j] * (1 + tol):
                touched = True
            if not long and highs[j] >= fast[j] * (1 - tol):
                touched = True
        if not touched:
            return False
        if long:
            return closes[k] > fast[k] and closes[k] > opens[k]
        return closes[k] < fast[k] and closes[k] < opens[k]

    def _trend_state(self, candles: pd.DataFrame, base: pd.Timedelta, price: float) -> tuple[int, float]:
        """(+1 si el precio está sobre la EMA de tendencia y -1 si está debajo, pendiente en %) con velas de contexto cerradas."""
        p: EmaCrossoverParams = self.params
        ctx_bar = pd.Timedelta(minutes=p.context_minutes)
        boundary = closed_bar_boundary(candles.index[-1], ctx_bar, base)
        key = int(boundary.value)
        if self._trend_cache is not None and self._trend_cache[0] == key:
            pass
        else:
            n_bars = p.trend_ema_period * 3 + p.trend_slope_lookback + 5
            bars = resample_closed_bars(candles, ctx_bar, boundary, n_bars)
            ema = bars["close"].ewm(span=p.trend_ema_period, adjust=False).mean().to_numpy(float) if len(bars) else np.array([])
            if len(ema) > p.trend_slope_lookback + 1:
                last = ema[-1]
                prev = ema[-1 - p.trend_slope_lookback]
                slope = (last - prev) / prev * 100 if prev else 0.0
                last_close = float(bars["close"].iloc[-1])
                self._trend_cache = (key, (last, slope, last_close))  # type: ignore[assignment]
            else:
                self._trend_cache = (key, None)  # type: ignore[assignment]
        cached = self._trend_cache[1]
        if cached is None:
            return 0, 0.0
        ema_last, slope, _ = cached
        return (1 if price > ema_last else -1 if price < ema_last else 0), slope
