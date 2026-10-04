import pandas as pd
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.indicators import atr_padded
from app.strategies.registry import register

_ATR_PERIOD = 14


class SessionBreakoutParams(BaseModel):
    use_asia: bool = Field(default=True, description="Operar el rango de la sesión asiática")
    asia_start: float = Field(default=0.0, ge=0.0, le=23.5, description="Inicio de la sesión asiática (hora UTC)")
    use_london: bool = Field(default=True, description="Operar el rango de la sesión de Londres")
    london_start: float = Field(default=7.0, ge=0.0, le=23.5, description="Inicio de la sesión de Londres (hora UTC)")
    use_newyork: bool = Field(default=True, description="Operar el rango de la sesión de Nueva York")
    newyork_start: float = Field(default=13.0, ge=0.0, le=23.5, description="Inicio de la sesión de Nueva York (hora UTC)")
    range_bars: int = Field(default=2, ge=1, le=12, description="Velas que forman el rango de apertura de cada sesión")
    entry_window_bars: int = Field(default=8, ge=1, le=48, description="Velas después del rango en las que se acepta la ruptura")
    range_min_atr: float = Field(default=0.5, ge=0.0, le=10.0, description="Rango mínimo como múltiplo del ATR (filtra rangos muertos)")
    range_max_atr: float = Field(default=3.0, ge=0.1, le=20.0, description="Rango máximo como múltiplo del ATR (filtra días de noticias)")
    rr_ratio: float = Field(default=2.0, ge=0.5, le=10.0, description="Take profit como múltiplo del riesgo (R)")
    stop_buffer_pct: float = Field(default=0.05, ge=0.0, le=2.0, description="Margen del stop más allá del borde opuesto del rango (%)")
    max_trades_per_day: int = Field(default=3, ge=1, le=10, description="Máximo de operaciones por día (UTC)")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


@register
class SessionBreakoutStrategy(Strategy):
    """Ruptura del rango de apertura de cada sesión (Asia, Londres, Nueva York).

    1. Al inicio de una sesión se forma su rango con las primeras `range_bars` velas.
    2. Si el rango tiene un tamaño razonable frente al ATR, se espera la primera vela que cierra fuera
       del rango durante `entry_window_bars` velas.
    3. Entra en esa ruptura (largo si cierra arriba, corto si cierra abajo). Stop del otro lado del
       rango; take profit a `rr_ratio` veces el riesgo.
    4. Una sola operación por sesión, y como máximo `max_trades_per_day` por día (UTC).

    Solo usa velas cerradas: el rango y la ruptura se confirman con cierres."""

    key = "session_breakout"
    display_name = "Ruptura del rango de apertura por sesión (Asia / Londres / Nueva York)"
    description = (
        "Marca el rango de las primeras velas de cada sesión (Asia, Londres, Nueva York) y opera la primera ruptura "
        "con cierre fuera del rango. Una operación por sesión, de 1 a 3 por día. Stop del otro lado del rango."
    )
    params_model = SessionBreakoutParams
    style = "Ruptura · sesiones"
    default_timeframe = "15"

    def __init__(self, params: BaseModel) -> None:
        super().__init__(params)
        self._taken: set[tuple[str, str]] = set()

    def _sessions(self) -> list[tuple[str, float]]:
        p: SessionBreakoutParams = self.params
        out = []
        if p.use_asia:
            out.append(("Asia", p.asia_start))
        if p.use_london:
            out.append(("Londres", p.london_start))
        if p.use_newyork:
            out.append(("Nueva York", p.newyork_start))
        return out

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        if ctx.position is not None:
            return None
        p: SessionBreakoutParams = self.params
        candles = ctx.candles
        if len(candles) < _ATR_PERIOD + 2:
            return None
        base = pd.Series(candles.index[-5:]).diff().median()
        if pd.isna(base) or base <= pd.Timedelta(0):
            return None
        bar = candles.iloc[-1]
        now = candles.index[-1]
        day = now.floor("1D")

        tail = candles.iloc[-400:]
        h = tail["high"].to_numpy(dtype=float)
        l = tail["low"].to_numpy(dtype=float)
        c = tail["close"].to_numpy(dtype=float)
        atr = atr_padded(h, l, c, _ATR_PERIOD)[-1]
        if pd.isna(atr) or atr <= 0:
            return None

        day_key = day.date().isoformat()
        taken_today = sum(1 for d, _ in self._taken if d == day_key)
        for name, start_hour in self._sessions():
            if taken_today >= p.max_trades_per_day:
                break
            session_open = day + pd.Timedelta(hours=start_hour)
            if session_open > now:
                session_open -= pd.Timedelta(days=1)
            key = (session_open.date().isoformat(), name)
            if key in self._taken:
                continue
            range_end = session_open + base * p.range_bars
            if now < range_end:
                continue  # el rango todavía se está formando
            window = candles.loc[(candles.index >= session_open) & (candles.index < range_end)]
            if len(window) < p.range_bars:
                continue
            r_high, r_low = float(window["high"].max()), float(window["low"].min())
            size = r_high - r_low
            if not (p.range_min_atr * atr <= size <= p.range_max_atr * atr):
                continue

            entry_end = range_end + base * p.entry_window_bars
            if now >= entry_end:
                continue
            after = candles.loc[(candles.index >= range_end) & (candles.index < now)]
            if len(after) and ((after["close"] > r_high).any() or (after["close"] < r_low).any()):
                self._taken.add(key)  # ya hubo ruptura en esta sesión: no se opera
                continue

            if bar["close"] > r_high:
                return self._signal(key, "buy", float(bar["close"]), r_low, name, r_high, r_low)
            if bar["close"] < r_low:
                return self._signal(key, "sell", float(bar["close"]), r_high, name, r_high, r_low)
        return None

    def _signal(self, key, action, price, opposite, name, r_high, r_low) -> Signal | None:
        p: SessionBreakoutParams = self.params
        buffer = p.stop_buffer_pct / 100
        stop = opposite * (1 - buffer) if action == "buy" else opposite * (1 + buffer)
        risk = price - stop if action == "buy" else stop - price
        if risk <= 0:
            return None
        target = price + p.rr_ratio * risk if action == "buy" else price - p.rr_ratio * risk
        self._taken.add(key)
        return Signal(
            action=action,
            reason=f"ruptura del rango {name} ({r_low:.2f}-{r_high:.2f})",
            stop_loss=stop,
            take_profit=target,
            risk_pct=p.risk_pct,
        )
