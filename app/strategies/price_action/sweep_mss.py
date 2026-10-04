from dataclasses import dataclass

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.indicators import atr_padded, closed_bar_boundary, market_regime, swing_indices
from app.strategies.registry import register


class SweepMssParams(BaseModel):
    context_minutes: int = Field(default=5, ge=2, le=240, description="Timeframe de contexto en minutos (estructura y liquidez); múltiplo del timeframe de la instancia")
    swing_n: int = Field(default=2, ge=1, le=10, description="Velas de contexto a cada lado para confirmar un swing")
    liquidity_lookback: int = Field(default=60, ge=10, le=300, description="Velas de contexto hacia atrás donde se busca la liquidez a barrer")
    use_prev_day_hl: bool = Field(default=True, description="Usar el máximo/mínimo del día anterior (UTC) como liquidez")
    use_swing_liquidity: bool = Field(default=True, description="Usar los swings confirmados como liquidez")
    use_equal_hl: bool = Field(default=True, description="Usar máximos/mínimos iguales (dos swings dentro de la tolerancia) como liquidez")
    equal_level_tolerance_pct: float = Field(default=0.05, ge=0.0, le=1.0, description="Tolerancia para considerar iguales dos niveles (% del precio)")
    sweep_min_penetration_pct: float = Field(default=0.02, ge=0.0, le=2.0, description="Penetración mínima de la mecha bajo el nivel (% del precio)")
    sweep_max_penetration_pct: float = Field(default=0.5, ge=0.0, le=5.0, description="Penetración máxima: si la mecha va más lejos no es barrida sino ruptura")
    sweep_confirmation_bars: int = Field(default=2, ge=1, le=10, description="Velas de contexto para que el cierre recupere el nivel barrido")
    mss_lookback: int = Field(default=12, ge=2, le=60, description="Velas de contexto máximas desde la barrida hasta el cambio de estructura (MSS)")
    use_displacement: bool = Field(default=True, description="Exigir una vela de desplazamiento fuerte en el movimiento del FVG")
    displacement_min_body_ratio: float = Field(default=0.6, ge=0.0, le=1.0, description="Cuerpo mínimo de la vela de desplazamiento (cuerpo / rango)")
    displacement_atr_mult: float = Field(default=1.0, ge=0.0, le=5.0, description="Cuerpo mínimo de la vela de desplazamiento (x ATR, 0 = sin filtro)")
    fvg_min_size_pct: float = Field(default=0.02, ge=0.0, le=2.0, description="Tamaño mínimo del FVG (% del precio)")
    fvg_entry_midpoint: bool = Field(default=False, description="Entrar en la mitad del FVG (si no, en el borde más cercano al precio)")
    retest_max_bars: int = Field(default=30, ge=1, le=300, description="Velas de la instancia para que el precio vuelva al FVG; si no, el setup se cancela")
    sl_buffer_pct: float = Field(default=0.05, ge=0.0, le=2.0, description="Margen del stop más allá de la mecha de la barrida (%)")
    tp_rr: float = Field(default=2.0, ge=0.5, le=10.0, description="Take profit como múltiplo del riesgo (R)")
    tp_at_opposite_liquidity: bool = Field(default=False, description="Tomar ganancia en la liquidez opuesta más cercana si está a más de 1R; si no, usa tp_rr")
    use_atr_filter: bool = Field(default=False, description="Operar solo si la volatilidad (ATR actual / promedio) está en el rango permitido")
    atr_period: int = Field(default=14, ge=2, le=100, description="Período del ATR (velas de contexto)")
    atr_ratio_min: float = Field(default=0.5, ge=0.0, le=10.0, description="ATR actual / ATR promedio: mínimo permitido")
    atr_ratio_max: float = Field(default=2.0, ge=0.0, le=10.0, description="ATR actual / ATR promedio: máximo permitido")
    use_volume_filter: bool = Field(default=False, description="Exigir volumen relativo alto en la vela de desplazamiento (RVOL)")
    rvol_period: int = Field(default=20, ge=2, le=200, description="Velas de contexto para el volumen promedio (RVOL)")
    rvol_min: float = Field(default=1.5, ge=0.0, le=10.0, description="RVOL mínimo: volumen de la vela de desplazamiento / volumen promedio")
    use_regime_filter: bool = Field(default=False, description="En tendencia alcista opera solo largos, en bajista solo cortos; en rango, ambos")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


@dataclass(frozen=True)
class _Setup:
    bullish: bool
    formed_at: pd.Timestamp  # fin de la vela de contexto que completa el FVG
    zone_low: float
    zone_high: float
    stop: float
    targets: tuple[float, ...]  # liquidez del lado opuesto, para el take profit
    reason: str


@register
class SweepMssStrategy(Strategy):
    """Price action: barrida de liquidez + cambio de estructura (MSS) + FVG + retroceso.

    Contexto (timeframe de contexto, 5m por defecto, remuestreado de las velas de la instancia):
    1. Liquidez: mínimos previos (día anterior, swings, mínimos iguales).
    2. Barrida: la mecha perfora el nivel por una penetración acotada y el cierre lo recupera.
    3. MSS: después de la barrida, un cierre supera el último swing alto (alcista; bajista al revés).
    4. Desplazamiento y FVG: una vela fuerte después del MSS deja un Fair Value Gap.

    Ejecución (timeframe de la instancia, 1m por defecto):
    5. Retroceso: el precio vuelve al FVG dentro de `retest_max_bars` velas y el cierre lo sostiene.
       Entra en el cierre de esa vela. El stop va detrás de la mecha de la barrida.

    Todo se calcula con velas cerradas: un swing solo existe cuando hay `swing_n` velas posteriores que
    lo confirman, y el contexto usado es el último completo. El corto es el espejo del largo."""

    key = "sweep_mss"
    display_name = "Barrida de liquidez + cambio de estructura (price action)"
    description = (
        "Price action multi-timeframe: espera que el precio barra un mínimo/máximo de liquidez en el contexto de 5 "
        "minutos, que luego cambie la estructura y deje un FVG con desplazamiento; entra en el retroceso al FVG "
        "en el timeframe de la instancia. Stop detrás de la barrida. Filtros opcionales de volatilidad, volumen y régimen."
    )
    params_model = SweepMssParams
    style = "Price action · estructura"
    default_timeframe = "1"

    def __init__(self, params: BaseModel) -> None:
        super().__init__(params)
        self._used: set[int] = set()
        self._setup_cache: tuple[int, list[_Setup]] | None = None
        self._prev_day_cache: dict[int, tuple[float, float]] = {}

    # ------------------------------------------------------------ senal

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        if ctx.position is not None:
            return None
        p: SweepMssParams = self.params
        candles = ctx.candles
        if len(candles) < 5:
            return None
        base = pd.Series(candles.index[-5:]).diff().median()
        ctf = pd.Timedelta(minutes=p.context_minutes)
        if pd.isna(base) or base <= pd.Timedelta(0) or ctf <= base or ctf % base != pd.Timedelta(0):
            return None  # el contexto debe ser un múltiplo exacto y mayor que el timeframe de la instancia

        setups = self._setups_for(candles, base, ctf)
        if not setups:
            return None

        last = candles.iloc[-1]
        recent = candles.iloc[-(p.retest_max_bars + 2):]
        for setup in setups:
            key = int(setup.formed_at.value)
            if key in self._used:
                continue
            since = recent[recent.index >= setup.formed_at]
            if len(since) == 0 or len(since) > p.retest_max_bars:
                continue
            signal = self._retest_signal(setup, since, last)
            if signal is not None:
                self._used.add(key)
                return signal
        return None

    def _retest_signal(self, setup: _Setup, since: pd.DataFrame, last: pd.Series) -> Signal | None:
        p: SweepMssParams = self.params
        zone_low, zone_high = setup.zone_low, setup.zone_high
        bull = setup.bullish
        level = (zone_low + zone_high) / 2 if p.fvg_entry_midpoint else (zone_high if bull else zone_low)

        def touches(bar) -> bool:
            if bull:
                return bar["low"] <= level and bar["close"] > zone_low
            return bar["high"] >= level and bar["close"] < zone_high

        def broken(bar) -> bool:
            return bar["close"] < zone_low if bull else bar["close"] > zone_high

        earlier = since.iloc[:-1]
        if any(broken(bar) or touches(bar) for _, bar in earlier.iterrows()):
            return None  # el setup se invalido o ya hubo retroceso antes (no es el primero)
        if not touches(last):
            return None

        entry = float(last["close"])
        risk = entry - setup.stop if bull else setup.stop - entry
        if risk <= 0:
            return None
        target = entry + p.tp_rr * risk if bull else entry - p.tp_rr * risk
        if p.tp_at_opposite_liquidity:
            beyond = [t for t in setup.targets if (t > entry + risk if bull else t < entry - risk)]
            if beyond:
                target = min(beyond) if bull else max(beyond)
        return Signal(
            action="buy" if bull else "sell",
            reason=f"{setup.reason} · retroceso al FVG ({zone_low:.2f}-{zone_high:.2f})",
            stop_loss=setup.stop,
            take_profit=target,
            risk_pct=p.risk_pct,
        )

    # ------------------------------------------------------------ contexto (una vez por vela de contexto)

    def _setups_for(self, candles: pd.DataFrame, base: pd.Timedelta, ctf: pd.Timedelta) -> list[_Setup]:
        boundary = closed_bar_boundary(candles.index[-1], ctf, base)
        key = int(boundary.value)
        if self._setup_cache is not None and self._setup_cache[0] == key:
            return self._setup_cache[1]
        setups = self._compute_setups(candles, boundary, ctf)
        self._setup_cache = (key, setups)
        return setups

    def _context_bars(self, candles: pd.DataFrame, boundary: pd.Timestamp, ctf: pd.Timedelta) -> pd.DataFrame:
        p: SweepMssParams = self.params
        n_bars = p.liquidity_lookback + p.mss_lookback + p.sweep_confirmation_bars + 4 * p.atr_period + p.rvol_period + 2 * p.swing_n + 20
        start = boundary - ctf * n_bars
        sub = candles.loc[(candles.index >= start) & (candles.index < boundary)]
        bars = sub.resample(ctf, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        return bars.dropna(subset=["close"])

    def _prev_day(self, candles: pd.DataFrame, boundary: pd.Timestamp) -> tuple[float, float] | None:
        day = boundary.floor("1D")
        key = int(day.value)
        if key not in self._prev_day_cache:
            prev = candles.loc[(candles.index >= day - pd.Timedelta("1D")) & (candles.index < day)]
            if len(prev) == 0:
                return None
            self._prev_day_cache[key] = (float(prev["high"].max()), float(prev["low"].min()))
            if len(self._prev_day_cache) > 10:
                self._prev_day_cache.pop(next(iter(self._prev_day_cache)))
        return self._prev_day_cache[key]

    def _compute_setups(self, candles: pd.DataFrame, boundary: pd.Timestamp, ctf: pd.Timedelta) -> list[_Setup]:
        p: SweepMssParams = self.params
        bars = self._context_bars(candles, boundary, ctf)
        if len(bars) < 2 * p.swing_n + p.atr_period + 5:
            return []
        o, h, l, c, v = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
        t = len(c) - 1
        atr = atr_padded(h, l, c, p.atr_period)

        if p.use_atr_filter:
            window = atr[max(0, t - 4 * p.atr_period + 1):t + 1]
            window = window[~np.isnan(window)]
            if np.isnan(atr[t]) or len(window) == 0 or window.mean() <= 0:
                return []
            ratio = atr[t] / window.mean()
            if not (p.atr_ratio_min <= ratio <= p.atr_ratio_max):
                return []

        trend = market_regime(h, l, p.swing_n) if p.use_regime_filter else 0
        prev = self._prev_day(candles, boundary) if p.use_prev_day_hl else None

        setups: list[_Setup] = []
        for bull in (True, False):
            if trend != 0 and (trend > 0) != bull:
                continue
            setup = self._side_setup(bull, bars.index, ctf, o, h, l, c, v, atr, prev)
            if setup is not None:
                setups.append(setup)
        setups.sort(key=lambda s: s.formed_at, reverse=True)
        return setups

    def _side_setup(self, bull, index, ctf, o, h, l, c, v, atr, prev) -> _Setup | None:
        """Busca el setup mas reciente de un lado. Todo se calcula en un espacio donde el lado alcista siempre
        vale igual: para el bajista se invierten los precios (x -> -x) y el máximo pasa a ser mínimo."""
        p: SweepMssParams = self.params
        s_sign = 1.0 if bull else -1.0
        O = s_sign * o
        C = s_sign * c
        H = h if bull else -l
        L = l if bull else -h
        t = len(C) - 1
        n = p.swing_n
        hi_idx, lo_idx = swing_indices(H, L, n)
        hi_idx, lo_idx = [int(i) for i in hi_idx], [int(i) for i in lo_idx]

        # niveles de liquidez del lado que se barre: (precio transformado, origen, confirmado en, texto)
        levels: list[tuple[float, int, int, str]] = []
        if p.use_swing_liquidity:
            levels += [(float(L[i]), i, i + n, "swing") for i in lo_idx]
        if p.use_equal_hl and len(lo_idx) >= 2:
            ref = abs(float(C[t])) or 1.0
            for a, b in zip(lo_idx, lo_idx[1:]):
                if abs(L[a] - L[b]) / ref * 100 <= p.equal_level_tolerance_pct:
                    levels.append((float(min(L[a], L[b])), a, b + n, "mínimos iguales"))
        if prev is not None:
            prev_level = prev[1] if bull else -prev[0]
            levels.append((float(prev_level), -1, -1, "mínimo del día anterior" if bull else "máximo del día anterior"))

        # niveles del lado opuesto, en precio real, para el take profit
        targets: list[float] = []
        if p.use_swing_liquidity:
            targets += [float(h[i] if bull else l[i]) for i in (hi_idx if bull else lo_idx)]
        if prev is not None:
            targets.append(float(prev[0] if bull else prev[1]))

        for s in range(t, max(-1, t - (p.mss_lookback + p.sweep_confirmation_bars + 10)), -1):
            for level, src, formed, text in levels:
                if formed >= s or (src >= 0 and src < s - p.liquidity_lookback):
                    continue
                if not L[s] < level:
                    continue
                pen = (level - L[s]) / abs(level) * 100 if level else 0.0
                if not (p.sweep_min_penetration_pct <= pen <= p.sweep_max_penetration_pct):
                    continue
                reclaim = next((r for r in range(s, min(s + p.sweep_confirmation_bars, t + 1)) if C[r] > level), None)
                if reclaim is None:
                    continue
                prior_highs = [i for i in hi_idx if i + n < s]
                if not prior_highs:
                    continue
                mss_level = H[max(prior_highs)]
                mss = next((m for m in range(reclaim + 1, min(s + p.mss_lookback, t) + 1) if C[m] > mss_level), None)
                if mss is None:
                    continue
                fvg = self._fvg_after(bull, index, ctf, s, reclaim, mss, t, O, C, H, L, v, atr)
                if fvg is not None:
                    level_price = s_sign * level
                    reason = (
                        f"{'LONG' if bull else 'SHORT'}: barrida de {text} {abs(level_price):.2f} · MSS sobre "
                        f"{abs(s_sign * mss_level):.2f} · desplazamiento"
                    )
                    return _Setup(
                        bullish=bull, formed_at=fvg[0], zone_low=fvg[1], zone_high=fvg[2],
                        stop=fvg[3], targets=tuple(targets), reason=reason,
                    )
        return None

    def _fvg_after(self, bull, index, ctf, s, reclaim, mss, t, O, C, H, L, v, atr):
        """FVG completado en la vela k >= mss, con la vela de desplazamiento k-1 y las tres velas k-2..k
        posteriores a la barrida. Devuelve (tiempo de k, zona baja, zona alta, stop) en precio real."""
        p: SweepMssParams = self.params
        for k in range(t, max(mss, s + 2) - 1, -1):
            if k - 2 < s:
                break
            zone_low_t, zone_high_t = H[k - 2], L[k]
            if not zone_high_t > zone_low_t:
                continue
            d = k - 1
            if not C[d] or (zone_high_t - zone_low_t) / abs(C[d]) * 100 < p.fvg_min_size_pct:
                continue
            body = C[d] - O[d]
            if body <= 0:
                continue
            rng = H[d] - L[d]
            if p.use_displacement:
                if rng <= 0 or body / rng < p.displacement_min_body_ratio:
                    continue
                if np.isnan(atr[d]) or body < p.displacement_atr_mult * atr[d]:
                    continue
            if p.use_volume_filter:
                if d - p.rvol_period < 0:
                    continue
                avg_vol = v[d - p.rvol_period:d].mean()
                if avg_vol <= 0 or v[d] / avg_vol < p.rvol_min:
                    continue
            if not np.all(L[k + 1:t + 1] > zone_high_t):
                continue  # el precio ya volvió al FVG: no es un retroceso fresco
            stop_ext_t = float(np.min(L[s:reclaim + 1]))
            stop_base = stop_ext_t if bull else -stop_ext_t
            stop = stop_base * (1 - p.sl_buffer_pct / 100) if bull else stop_base * (1 + p.sl_buffer_pct / 100)
            if bull:
                zone_low, zone_high = zone_low_t, zone_high_t
            else:
                zone_low, zone_high = -zone_high_t, -zone_low_t
            if (bull and stop >= zone_low) or (not bull and stop <= zone_high):
                continue
            return index[k] + ctf, float(zone_low), float(zone_high), float(stop)
        return None
