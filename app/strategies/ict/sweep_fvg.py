from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.registry import register

_NY = ZoneInfo("America/New_York")
_ATR_PERIOD = 14


class IctSweepFvgParams(BaseModel):
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")
    rr_ratio: float = Field(default=2.0, ge=0.5, le=10.0, description="Take profit como múltiplo del riesgo (R)")
    swing_n: int = Field(default=3, ge=2, le=10, description="Velas a cada lado para confirmar un swing (liquidez)")
    liquidity_lookback: int = Field(default=48, ge=10, le=300, description="Cuántas velas atrás buscar liquidez a barrer")
    max_bars_after_sweep: int = Field(default=12, ge=3, le=60, description="Máximo de velas entre la barrida y la entrada")
    displacement_atr_mult: float = Field(default=1.0, ge=0.0, le=5.0, description="Cuerpo minimo de la vela de desplazamiento (x ATR14, 0 = sin filtro)")
    min_fvg_pct: float = Field(default=0.03, ge=0.0, le=2.0, description="Tamaño mínimo del FVG (% del precio)")
    sl_buffer_pct: float = Field(default=0.05, ge=0.0, le=2.0, description="Margen del stop más allá de la mecha de la barrida (%)")
    use_htf_bias: bool = Field(default=True, description="Filtrar por sesgo de estructura en 4h")
    htf_swing_n: int = Field(default=2, ge=1, le=5, description="Velas de 4h a cada lado para confirmar un swing del sesgo")
    use_killzones: bool = Field(default=True, description="Operar solo dentro de las kill zones (hora de Nueva York)")
    london_start: int = Field(default=2, ge=0, le=24, description="Kill zone Londres: inicio (hora NY)")
    london_end: int = Field(default=5, ge=0, le=24, description="Kill zone Londres: fin (hora NY)")
    ny_start: int = Field(default=7, ge=0, le=24, description="Kill zone Nueva York: inicio (hora NY)")
    ny_end: int = Field(default=10, ge=0, le=24, description="Kill zone Nueva York: fin (hora NY)")


def _swing_indices(highs: np.ndarray, lows: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Indices de swing highs / swing lows confirmados: extremo estricto
    (sin empates) frente a n velas a cada lado."""
    empty = np.array([], dtype=int)
    if len(highs) < 2 * n + 1:
        return empty, empty
    win_h = sliding_window_view(highs, 2 * n + 1)
    win_l = sliding_window_view(lows, 2 * n + 1)
    center_h = win_h[:, n]
    center_l = win_l[:, n]
    is_high = (center_h == win_h.max(axis=1)) & ((win_h == center_h[:, None]).sum(axis=1) == 1)
    is_low = (center_l == win_l.min(axis=1)) & ((win_l == center_l[:, None]).sum(axis=1) == 1)
    return np.nonzero(is_high)[0] + n, np.nonzero(is_low)[0] + n


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    prev_close = np.concatenate(([closes[0]], closes[:-1]))
    tr = np.maximum.reduce([highs - lows, np.abs(highs - prev_close), np.abs(lows - prev_close)])
    atr = np.full(len(tr), np.nan)
    if len(tr) >= _ATR_PERIOD:
        cs = np.concatenate(([0.0], np.cumsum(tr)))
        atr[_ATR_PERIOD - 1:] = (cs[_ATR_PERIOD:] - cs[:-_ATR_PERIOD]) / _ATR_PERIOD
    return atr


@register
class IctSweepFvgStrategy(Strategy):
    """ICT: barrida de liquidez + Fair Value Gap.

    Largo (el corto es simetrico):
    1. Sesgo: en 4h, maximos y minimos mas altos (HH + HL) -> solo largos.
    2. Liquidez: un swing low reciente es barrido (la mecha lo perfora) y la
       vela cierra de nuevo por encima (rechazo).
    3. Desplazamiento: una vela alcista fuerte deja un FVG alcista (el minimo
       de la vela 3 queda por encima del maximo de la vela 1).
    4. Entrada: primer retroceso al FVG; se entra si la vela toca la zona y
       cierra por encima de su base. Stop bajo la mecha de la barrida, take
       profit en un multiplo del riesgo.
    5. Horario: solo dentro de las kill zones (hora de Nueva York)."""

    key = "ict_sweep_fvg"
    display_name = "ICT: Barrida de liquidez + FVG"
    description = (
        "Espera que el precio barra un máximo/mínimo reciente (liquidez) y deje un Fair Value Gap; entra en el "
        "retroceso al FVG a favor del sesgo de 4h, solo en las kill zones de Londres y Nueva York. Stop bajo la "
        "mecha de la barrida y take profit en múltiplo del riesgo."
    )
    params_model = IctSweepFvgParams

    def __init__(self, params: BaseModel) -> None:
        super().__init__(params)
        self._used_sweeps: set[int] = set()
        self._htf_cache: dict[int, int] = {}

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        if ctx.position is not None:
            return None
        p: IctSweepFvgParams = self.params

        candles = ctx.candles
        ts = candles.index[-1]
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")

        if p.use_killzones and not self._in_killzone(ts):
            return None

        bias = 0
        if p.use_htf_bias:
            bias = self._htf_bias(candles, ts)
            if bias == 0:
                return None

        window_size = p.liquidity_lookback + p.max_bars_after_sweep + 2 * p.swing_n + 20
        w = candles.iloc[-window_size:]
        if len(w) < _ATR_PERIOD + 5:
            return None

        o = w["open"].to_numpy(dtype=float)
        h = w["high"].to_numpy(dtype=float)
        l = w["low"].to_numpy(dtype=float)
        c = w["close"].to_numpy(dtype=float)
        atr = _atr(h, l, c)
        swing_highs, swing_lows = _swing_indices(h, l, p.swing_n)

        if bias >= 0:
            signal = self._find_setup(True, w, o, h, l, c, atr, swing_lows)
            if signal is not None:
                return signal
        if bias <= 0:
            return self._find_setup(False, w, o, h, l, c, atr, swing_highs)
        return None

    def _in_killzone(self, ts: pd.Timestamp) -> bool:
        p: IctSweepFvgParams = self.params
        ny = ts.tz_convert(_NY)
        hour = ny.hour + ny.minute / 60
        return (p.london_start <= hour < p.london_end) or (p.ny_start <= hour < p.ny_end)

    def _htf_bias(self, candles: pd.DataFrame, ts: pd.Timestamp) -> int:
        """1 = alcista (HH+HL), -1 = bajista (LH+LL), 0 = sin sesgo. Se calcula
        con velas de 4h ya cerradas y se cachea por bucket de 4h."""
        p: IctSweepFvgParams = self.params
        bucket = ts.tz_convert("UTC").floor("4h")
        key = int(bucket.value)
        if key in self._htf_cache:
            return self._htf_cache[key]

        bias = 0
        idx = candles.index
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        recent = candles.iloc[-1500:]
        recent_idx = idx[-len(recent):]
        closed = recent[recent_idx < bucket]
        if len(closed) > 0:
            closed = closed.set_axis(recent_idx[recent_idx < bucket])
            htf = closed.resample("4h").agg({"high": "max", "low": "min"}).dropna()
            hh, ll = _swing_indices(htf["high"].to_numpy(dtype=float), htf["low"].to_numpy(dtype=float), p.htf_swing_n)
            if len(hh) >= 2 and len(ll) >= 2:
                highs = htf["high"].to_numpy(dtype=float)[hh[-2:]]
                lows = htf["low"].to_numpy(dtype=float)[ll[-2:]]
                if highs[1] > highs[0] and lows[1] > lows[0]:
                    bias = 1
                elif highs[1] < highs[0] and lows[1] < lows[0]:
                    bias = -1

        self._htf_cache[key] = bias
        return bias

    def _find_setup(
        self,
        bullish: bool,
        w: pd.DataFrame,
        o: np.ndarray,
        h: np.ndarray,
        l: np.ndarray,
        c: np.ndarray,
        atr: np.ndarray,
        swings: np.ndarray,
    ) -> Signal | None:
        p: IctSweepFvgParams = self.params
        t = len(c) - 1
        n = p.swing_n

        for s in range(t - p.max_bars_after_sweep, t - 2):
            if s < 0:
                continue
            sweep_key = int(w.index[s].value)
            if sweep_key in self._used_sweeps:
                continue

            eligible = swings[(swings + n < s) & (swings >= s - p.liquidity_lookback)]
            if len(eligible) == 0:
                continue
            if bullish:
                levels = l[eligible]
                swept = np.any((levels > l[s]) & (levels < c[s]))
            else:
                levels = h[eligible]
                swept = np.any((levels < h[s]) & (levels > c[s]))
            if not swept:
                continue

            for k in range(s + 2, t):
                if bullish:
                    zone_low, zone_high = h[k - 2], l[k]
                    valid_gap = zone_high > zone_low
                    body = c[k - 1] - o[k - 1]
                else:
                    zone_low, zone_high = h[k], l[k - 2]
                    valid_gap = zone_high > zone_low
                    body = o[k - 1] - c[k - 1]
                if not valid_gap:
                    continue
                if (zone_high - zone_low) / c[k - 1] * 100 < p.min_fvg_pct:
                    continue
                if p.displacement_atr_mult > 0:
                    if np.isnan(atr[k - 1]) or body < p.displacement_atr_mult * atr[k - 1]:
                        continue
                elif body <= 0:
                    continue

                # primer retroceso: ninguna vela entre el FVG y ahora lo toco antes
                if bullish:
                    untouched = bool(np.all(l[k + 1:t] > zone_high))
                    tapped_now = l[t] <= zone_high and c[t] > zone_low
                else:
                    untouched = bool(np.all(h[k + 1:t] < zone_low))
                    tapped_now = h[t] >= zone_low and c[t] < zone_high
                if not (untouched and tapped_now):
                    continue

                entry_ref = c[t]
                if bullish:
                    stop = float(np.min(l[s:t + 1])) * (1 - p.sl_buffer_pct / 100)
                    if stop >= entry_ref:
                        continue
                    target = entry_ref + p.rr_ratio * (entry_ref - stop)
                    action = "buy"
                else:
                    stop = float(np.max(h[s:t + 1])) * (1 + p.sl_buffer_pct / 100)
                    if stop <= entry_ref:
                        continue
                    target = entry_ref - p.rr_ratio * (stop - entry_ref)
                    action = "sell"

                self._used_sweeps.add(sweep_key)
                side = "alcista" if bullish else "bajista"
                return Signal(
                    action=action,
                    reason=f"barrida {side} + FVG ({zone_low:.2f}-{zone_high:.2f})",
                    stop_loss=stop,
                    take_profit=target,
                    risk_pct=p.risk_pct,
                )
        return None
