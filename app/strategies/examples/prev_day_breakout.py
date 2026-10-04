import pandas as pd
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.indicators import atr_padded
from app.strategies.registry import register

_ATR_PERIOD = 14


class PrevDayBreakoutParams(BaseModel):
    use_london: bool = Field(default=True, description="Operar en la ventana de Londres")
    london_start: float = Field(default=7.0, ge=0.0, le=23.5, description="Inicio de la ventana de Londres (hora UTC)")
    use_newyork: bool = Field(default=True, description="Operar en la ventana de Nueva York")
    newyork_start: float = Field(default=13.0, ge=0.0, le=23.5, description="Inicio de la ventana de Nueva York (hora UTC)")
    entry_window_bars: int = Field(default=16, ge=1, le=96, description="Velas de 15 minutos de cada ventana en las que se acepta la ruptura")
    stop_atr_mult: float = Field(default=1.5, ge=0.2, le=10.0, description="Stop a N × ATR de la entrada")
    rr_ratio: float = Field(default=2.0, ge=0.5, le=10.0, description="Take profit como múltiplo del riesgo (R)")
    max_trades_per_day: int = Field(default=3, ge=1, le=10, description="Máximo de operaciones por día (UTC)")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


@register
class PrevDayBreakoutStrategy(Strategy):
    """Ruptura de los máximos y mínimos del día anterior, dentro de las ventanas de Londres y Nueva York.

    1. Niveles: máximo y mínimo del día UTC anterior.
    2. Disparo: la primera vela de 15 minutos del día que cierra fuera de un nivel, dentro de una ventana
       de sesión. Si el precio ya había cerrado fuera antes en el día, ese nivel no se opera.
    3. Entrada al cierre de esa vela. Stop a `stop_atr_mult` × ATR; take profit a `rr_ratio` veces el riesgo.
    4. Como máximo `max_trades_per_day` operaciones por día (UTC).

    Solo usa velas cerradas: los niveles son del día anterior completo."""

    key = "prev_day_breakout"
    display_name = "Ruptura de máximos y mínimos del día anterior (Londres / Nueva York)"
    description = (
        "Opera la primera ruptura del máximo o mínimo del día anterior dentro de las ventanas de Londres y Nueva York. "
        "Cada nivel se opera una sola vez por día. Stop por ATR, objetivo en múltiplo del riesgo."
    )
    params_model = PrevDayBreakoutParams
    style = "Ruptura · niveles diarios"
    default_timeframe = "15"

    def __init__(self, params: BaseModel) -> None:
        super().__init__(params)
        self._taken: set[tuple[str, str]] = set()

    def _sessions(self) -> list[tuple[str, float]]:
        p: PrevDayBreakoutParams = self.params
        out = []
        if p.use_london:
            out.append(("Londres", p.london_start))
        if p.use_newyork:
            out.append(("Nueva York", p.newyork_start))
        return out

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        if ctx.position is not None:
            return None
        p: PrevDayBreakoutParams = self.params
        candles = ctx.candles
        if len(candles) < _ATR_PERIOD + 2:
            return None
        base = pd.Series(candles.index[-5:]).diff().median()
        if pd.isna(base) or base != pd.Timedelta(minutes=15):
            return None

        now = candles.index[-1]
        day = now.floor("1D")
        day_key = day.date().isoformat()
        if sum(1 for d, _ in self._taken if d == day_key) >= p.max_trades_per_day:
            return None

        prev = candles.loc[(candles.index >= day - pd.Timedelta(days=1)) & (candles.index < day)]
        if len(prev) == 0:
            return None
        prev_high, prev_low = float(prev["high"].max()), float(prev["low"].min())

        today = candles.loc[(candles.index >= day) & (candles.index < now)]
        bar = candles.iloc[-1]
        close = float(bar["close"])

        tail = candles.iloc[-400:]
        atr = atr_padded(tail["high"].to_numpy(float), tail["low"].to_numpy(float), tail["close"].to_numpy(float), _ATR_PERIOD)[-1]
        if pd.isna(atr) or atr <= 0:
            return None

        for name, start_hour in self._sessions():
            session_open = day + pd.Timedelta(hours=start_hour)
            if not (session_open <= now < session_open + base * p.entry_window_bars):
                continue
            for side, level in (("buy", prev_high), ("sell", prev_low)):
                key = (day_key, side)
                if key in self._taken:
                    continue
                if side == "buy" and not close > level:
                    continue
                if side == "sell" and not close < level:
                    continue
                if len(today) and ((today["close"] > level).any() if side == "buy" else (today["close"] < level).any()):
                    self._taken.add(key)  # el nivel ya se había roto antes en el día
                    continue
                return self._signal(key, side, close, atr, name, level)
        return None

    def _signal(self, key, side: str, price: float, atr: float, name: str, level: float) -> Signal | None:
        p: PrevDayBreakoutParams = self.params
        stop_dist = atr * p.stop_atr_mult
        stop = price - stop_dist if side == "buy" else price + stop_dist
        target = price + p.rr_ratio * stop_dist if side == "buy" else price - p.rr_ratio * stop_dist
        self._taken.add(key)
        return Signal(
            action=side,
            reason=f"ruptura del {'máximo' if side == 'buy' else 'mínimo'} del día anterior ({level:.2f}) en ventana {name}",
            stop_loss=stop,
            take_profit=target,
            risk_pct=p.risk_pct,
        )
