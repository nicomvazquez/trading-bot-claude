import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.indicators import swing_indices
from app.strategies.registry import register


class DaytrendSessionParams(BaseModel):
    daily_ema_period: int = Field(default=20, ge=5, le=200, description="EMA diaria que define la tendencia del día")
    pullback_ema_period: int = Field(default=20, ge=5, le=200, description="EMA de 15 minutos a la que se espera el retroceso")
    use_london: bool = Field(default=True, description="Operar en la ventana de Londres")
    london_start: float = Field(default=7.0, ge=0.0, le=23.5, description="Inicio de la ventana de Londres (hora UTC)")
    use_newyork: bool = Field(default=True, description="Operar en la ventana de Nueva York")
    newyork_start: float = Field(default=13.0, ge=0.0, le=23.5, description="Inicio de la ventana de Nueva York (hora UTC)")
    entry_window_bars: int = Field(default=16, ge=2, le=96, description="Velas de 15 minutos de cada ventana en las que se busca el retroceso")
    swing_n: int = Field(default=2, ge=1, le=10, description="Velas a cada lado para confirmar el swing del stop")
    swing_lookback: int = Field(default=96, ge=5, le=300, description="Velas hacia atrás donde buscar el swing del stop (96 = un día de 15 minutos)")
    stop_buffer_pct: float = Field(default=0.05, ge=0.0, le=2.0, description="Margen del stop más allá del swing (%)")
    rr_ratio: float = Field(default=2.0, ge=1.5, le=10.0, description="Take profit como múltiplo del riesgo (R)")
    max_trades_per_day: int = Field(default=2, ge=1, le=10, description="Máximo de operaciones por día (UTC)")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


def _daily_trend(closes_daily: pd.Series, period: int) -> int:
    """+1 si el cierre diario está sobre la EMA y la EMA sube; -1 en el caso opuesto; 0 si no hay tendencia clara."""
    if len(closes_daily) < period + 2:
        return 0
    ema = closes_daily.ewm(span=period, adjust=False).mean()
    if closes_daily.iloc[-1] > ema.iloc[-1] and ema.iloc[-1] > ema.iloc[-2]:
        return 1
    if closes_daily.iloc[-1] < ema.iloc[-1] and ema.iloc[-1] < ema.iloc[-2]:
        return -1
    return 0


@register
class DaytrendSessionStrategy(Strategy):
    """Tendencia intradía con sesgo diario.

    1. Sesgo: el cierre diario (último día completo) está sobre su EMA y la EMA sube → solo largos;
       lo opuesto → solo cortos; sin tendencia clara, no opera.
    2. Ventanas: al inicio de Londres y de Nueva York se abre una ventana de `entry_window_bars` velas de 15 min.
    3. Retroceso: en tendencia alcista, el precio baja hasta la EMA de 15 min dentro de la ventana y una
       vela cierra de nuevo por encima de la EMA y en alza. Corto en el caso opuesto.
    4. Stop: debajo del último swing bajo confirmado (encima del último alto en cortos), más un margen.
       Take profit a `rr_ratio` veces el riesgo.
    5. Como máximo una operación por ventana y `max_trades_per_day` por día (UTC).

    Solo usa velas cerradas: el sesgo usa días completos anteriores al día actual."""

    key = "daytrend_session"
    display_name = "Tendencia intradía con sesgo diario (Londres / Nueva York)"
    description = (
        "Opera a favor de la tendencia diaria (EMA diaria) en las ventanas de Londres y Nueva York: espera un retroceso "
        "a la EMA de 15 minutos y entra cuando el precio vuelve a cerrar a favor. Stop en el último swing, objetivo a "
        "2R, hasta 2 operaciones por día."
    )
    params_model = DaytrendSessionParams
    style = "Tendencia · daytrading"
    default_timeframe = "15"

    def __init__(self, params: BaseModel) -> None:
        super().__init__(params)
        self._taken: set[tuple[str, str]] = set()
        self._trend_cache: dict[int, int] = {}

    def _sessions(self) -> list[tuple[str, float]]:
        p: DaytrendSessionParams = self.params
        out = []
        if p.use_london:
            out.append(("Londres", p.london_start))
        if p.use_newyork:
            out.append(("Nueva York", p.newyork_start))
        return out

    def _trend(self, candles: pd.DataFrame, day_start: pd.Timestamp) -> int:
        p: DaytrendSessionParams = self.params
        key = int(day_start.value)
        if key in self._trend_cache:
            return self._trend_cache[key]
        past = candles.loc[(candles.index < day_start) & (candles.index >= day_start - pd.Timedelta(days=p.daily_ema_period * 4))]
        daily = past["close"].resample("1D", label="left", closed="left").last().dropna()
        trend = _daily_trend(daily, p.daily_ema_period)
        if len(self._trend_cache) > 2000:
            self._trend_cache.clear()
        self._trend_cache[key] = trend
        return trend

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        if ctx.position is not None:
            return None
        p: DaytrendSessionParams = self.params
        candles = ctx.candles
        if len(candles) < p.pullback_ema_period * 3:
            return None
        base = pd.Series(candles.index[-5:]).diff().median()
        if pd.isna(base) or base != pd.Timedelta(minutes=15):
            return None  # la estrategia está pensada para velas de 15 minutos

        now = candles.index[-1]
        day = now.floor("1D")
        trend = self._trend(candles, day)
        if trend == 0:
            return None

        day_key = day.date().isoformat()
        if sum(1 for d, _ in self._taken if d == day_key) >= p.max_trades_per_day:
            return None

        for name, start_hour in self._sessions():
            session_open = day + pd.Timedelta(hours=start_hour)
            window_end = session_open + base * p.entry_window_bars
            if not (session_open <= now < window_end):
                continue
            key = (day_key, name)
            if key in self._taken:
                continue
            return self._pullback_signal(candles, session_open, key, name, trend)
        return None

    def _pullback_signal(self, candles: pd.DataFrame, session_open, key, name: str, trend: int) -> Signal | None:
        p: DaytrendSessionParams = self.params
        tail = candles.iloc[-(p.pullback_ema_period * 4 + p.entry_window_bars + 5):]
        ema = tail["close"].ewm(span=p.pullback_ema_period, adjust=False).mean()
        win_mask = tail.index >= session_open
        if not win_mask.any():
            return None
        window = tail.loc[win_mask]
        ema_w = ema.loc[win_mask]
        last = window.iloc[-1]
        ema_now = float(ema_w.iloc[-1])

        if trend > 0:
            pulled = bool((window["low"].iloc[:-1] <= ema_w.iloc[:-1]).any()) if len(window) > 1 else False
            if not (pulled and last["close"] > ema_now and last["close"] > last["open"]):
                return None
        else:
            pulled = bool((window["high"].iloc[:-1] >= ema_w.iloc[:-1]).any()) if len(window) > 1 else False
            if not (pulled and last["close"] < ema_now and last["close"] < last["open"]):
                return None

        lookback = candles.iloc[-p.swing_lookback:]
        h = lookback["high"].to_numpy(dtype=float)
        l = lookback["low"].to_numpy(dtype=float)
        hi_idx, lo_idx = swing_indices(h, l, p.swing_n)
        entry = float(last["close"])
        buf = p.stop_buffer_pct / 100
        if trend > 0:
            if len(lo_idx) == 0:
                return None
            stop = float(l[lo_idx[-1]]) * (1 - buf)
            risk = entry - stop
            if risk <= 0:
                return None
            target = entry + p.rr_ratio * risk
            action = "buy"
            side = "alcista"
        else:
            if len(hi_idx) == 0:
                return None
            stop = float(h[hi_idx[-1]]) * (1 + buf)
            risk = stop - entry
            if risk <= 0:
                return None
            target = entry - p.rr_ratio * risk
            action = "sell"
            side = "bajista"

        self._taken.add(key)
        return Signal(
            action=action,
            reason=f"tendencia diaria {side}, retroceso a la EMA {p.pullback_ema_period} en ventana {name}",
            stop_loss=stop,
            take_profit=target,
            risk_pct=p.risk_pct,
        )
