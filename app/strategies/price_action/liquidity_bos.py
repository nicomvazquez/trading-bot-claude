from dataclasses import dataclass

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.indicators import atr_padded, closed_bar_boundary, market_regime, resample_closed_bars, swing_indices
from app.strategies.registry import register


class LiquidityBosParams(BaseModel):
    context_minutes: int = Field(default=60, ge=15, le=1440, description="Timeframe de contexto (tendencia), en minutos; múltiplo del timeframe de estructura")
    structure_minutes: int = Field(default=15, ge=5, le=240, description="Timeframe de estructura (swings, sweeps y BOS), en minutos; múltiplo del timeframe de ejecución")
    swing_n: int = Field(default=2, ge=1, le=10, description="Velas de estructura a cada lado para confirmar un swing")
    swing_lookback: int = Field(default=60, ge=10, le=300, description="Velas de estructura hacia atrás donde se buscan niveles de liquidez")
    regime_filter_enabled: bool = Field(default=False, description="Usar el contexto para filtrar la dirección")
    allow_trend_trades: bool = Field(default=True, description="Con filtro activo: operar a favor de la tendencia del contexto")
    allow_reversals: bool = Field(default=False, description="Con filtro activo: permitir operaciones contra la tendencia y en rango")
    use_previous_day_levels: bool = Field(default=True, description="Usar máximo/mínimo del día anterior (UTC) como liquidez")
    use_session_levels: bool = Field(default=True, description="Usar máximo/mínimo del día actual (UTC) como liquidez")
    use_swing_levels: bool = Field(default=True, description="Usar swings confirmados como liquidez")
    use_equal_levels: bool = Field(default=True, description="Usar máximos/mínimos iguales como liquidez")
    liquidity_tolerance_pct: float = Field(default=0.05, ge=0.0, le=1.0, description="Tolerancia para considerar iguales dos niveles (% del precio)")
    sweep_min_penetration_pct: float = Field(default=0.02, ge=0.0, le=2.0, description="Penetración mínima bajo/sobre el nivel (% del precio)")
    sweep_max_penetration_pct: float = Field(default=0.5, ge=0.0, le=5.0, description="Penetración máxima: si va más lejos no es barrida sino ruptura")
    sweep_confirmation_bars: int = Field(default=2, ge=1, le=10, description="Velas de estructura para que el cierre recupere el nivel barrido")
    mss_lookback: int = Field(default=12, ge=2, le=60, description="Velas de estructura máximas desde la barrida hasta el BOS")
    mss_min_break_pct: float = Field(default=0.0, ge=0.0, le=2.0, description="Distancia mínima de cierre sobre el swing roto (% del precio)")
    retest_tolerance_pct: float = Field(default=0.05, ge=0.0, le=2.0, description="Ancho de la zona de retest alrededor del nivel roto (% del precio)")
    max_retest_bars: int = Field(default=36, ge=1, le=300, description="Velas de ejecución para que llegue el retest; si no, se cancela el setup")
    require_confirmation_candle: bool = Field(default=True, description="Exigir una vela de confirmación alcista (bajista en cortos) que cierre fuera de la zona")
    volume_filter_enabled: bool = Field(default=False, description="Exigir volumen relativo mínimo en la vela de confirmación")
    volume_period: int = Field(default=20, ge=2, le=200, description="Velas de ejecución para el volumen promedio (RVOL)")
    minimum_rvol: float = Field(default=1.5, ge=0.0, le=10.0, description="RVOL mínimo de la vela de confirmación")
    volatility_filter_enabled: bool = Field(default=False, description="Operar solo con volatilidad dentro del rango permitido")
    atr_period: int = Field(default=14, ge=2, le=100, description="Período del ATR de ejecución")
    minimum_volatility: float = Field(default=0.5, ge=0.0, le=10.0, description="ATR actual / ATR promedio: mínimo permitido")
    maximum_volatility: float = Field(default=2.0, ge=0.0, le=10.0, description="ATR actual / ATR promedio: máximo permitido")
    stop_buffer_pct: float = Field(default=0.05, ge=0.0, le=2.0, description="Margen del stop más allá del extremo del sweep (%)")
    minimum_risk_reward: float = Field(default=2.0, ge=0.5, le=10.0, description="Relación mínima entre el objetivo en liquidez y el riesgo; si no se cumple, no se opera")
    max_trades_per_day: int = Field(default=2, ge=1, le=10, description="Máximo de operaciones por día (UTC)")
    max_long_trades_per_day: int = Field(default=2, ge=0, le=10, description="Máximo de largos por día (UTC)")
    max_short_trades_per_day: int = Field(default=2, ge=0, le=10, description="Máximo de cortos por día (UTC)")
    cooldown_after_trade_bars: int = Field(default=12, ge=0, le=500, description="Velas de ejecución de espera después de cada entrada")
    session_filter_enabled: bool = Field(default=False, description="Operar solo dentro de la ventana horaria indicada (hora UTC)")
    session_start_hour: float = Field(default=0.0, ge=0.0, le=24.0, description="Inicio de la ventana horaria (UTC)")
    session_end_hour: float = Field(default=24.0, ge=0.0, le=24.0, description="Fin de la ventana horaria (UTC)")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


@dataclass(frozen=True)
class _Setup:
    side: str
    formed_at: pd.Timestamp  # fin de la vela de estructura que confirma el BOS
    level: float  # swing roto
    zone_low: float
    zone_high: float
    stop_base: float  # extremo del sweep
    targets: tuple[float, ...]  # liquidez del lado opuesto
    reason: str


@register
class LiquidityBosStrategy(Strategy):
    """Liquidez → barrida → BOS → retest → confirmación, en tres timeframes.

    Contexto (1 h): tendencia por HH/HL o LH/LL. Filtra la dirección si está activado.
    Estructura (15 m): niveles de liquidez (día anterior, día actual, swings, mínimos/máximos iguales),
      barrida con recuperación, y BOS: un cierre supera el último swing contrario antes de la barrida.
    Ejecución (5 m): el precio vuelve a la zona del nivel roto (retest) y una vela de confirmación cierra
      fuera de la zona a favor. Entrada al cierre; stop bajo la barrida; objetivo en liquidez opuesta
      solo si la relación riesgo/beneficio cumple el mínimo.

    Todo se calcula con velas cerradas. Un swing solo existe cuando hay `swing_n` velas posteriores que lo confirman."""

    key = "liquidity_bos_retest"
    display_name = "Liquidez + barrida + BOS + retest (price action, 1h / 15m / 5m)"
    description = (
        "Espera una barrida de liquidez, un cambio de estructura (BOS) y un retest del nivel roto. Entra con una vela "
        "de confirmación en 5 minutos, con stop bajo la barrida y objetivo en la liquidez opuesta. Pocas operaciones, "
        "con relación riesgo/beneficio mínima configurable."
    )
    params_model = LiquidityBosParams
    style = "Price action · multi-timeframe"
    default_timeframe = "5"

    def __init__(self, params: BaseModel) -> None:
        super().__init__(params)
        self._used: set[int] = set()
        self._entries: list[tuple[str, str]] = []  # (día, lado) de cada entrada
        self._last_entry: pd.Timestamp | None = None
        self._struct_cache: tuple[int, list[_Setup]] | None = None
        self._regime_cache: tuple[int, int] | None = None
        self._day_cache: dict[int, tuple[float, float]] = {}

    # ------------------------------------------------------------ señal

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        if ctx.position is not None:
            return None
        p: LiquidityBosParams = self.params
        candles = ctx.candles
        if len(candles) < 60:
            return None
        base = pd.Series(candles.index[-5:]).diff().median()
        structure = pd.Timedelta(minutes=p.structure_minutes)
        context = pd.Timedelta(minutes=p.context_minutes)
        if pd.isna(base) or base <= pd.Timedelta(0) or not (base < structure < context):
            return None
        if structure % base != pd.Timedelta(0) or context % structure != pd.Timedelta(0):
            return None  # cada timeframe tiene que ser múltiplo exacto del anterior

        now = candles.index[-1]
        if p.session_filter_enabled and not self._in_session(now):
            return None

        setups = self._setups_for(candles, base, structure)
        if not setups:
            return None
        regime = self._regime_for(candles, base, context)
        day = now.floor("1D")
        day_key = day.date().isoformat()
        day_entries = [e for e in self._entries if e[0] == day_key]
        if len(day_entries) >= p.max_trades_per_day:
            return None
        if self._last_entry is not None and now - self._last_entry < base * p.cooldown_after_trade_bars:
            return None

        recent = candles.iloc[-(p.max_retest_bars + 2):]
        for setup in setups:
            key = int(setup.formed_at.value)
            if key in self._used or not self._side_allowed(setup.side, regime):
                continue
            if sum(1 for _, s in day_entries if s == setup.side) >= self._side_cap(setup.side):
                continue
            since = recent[recent.index >= setup.formed_at]
            if len(since) == 0 or len(since) > p.max_retest_bars:
                continue
            signal = self._entry_signal(setup, since, candles, day_key)
            if signal is not None:
                self._used.add(key)
                self._entries.append((day_key, setup.side))
                self._last_entry = now
                return signal
        return None

    def _side_allowed(self, side: str, regime: int) -> bool:
        p: LiquidityBosParams = self.params
        if not p.regime_filter_enabled:
            return True
        if regime == 0:
            return p.allow_reversals
        with_trend = (side == "buy") == (regime > 0)
        return p.allow_trend_trades if with_trend else p.allow_reversals

    def _side_cap(self, side: str) -> int:
        p: LiquidityBosParams = self.params
        return p.max_long_trades_per_day if side == "buy" else p.max_short_trades_per_day

    def _in_session(self, ts: pd.Timestamp) -> bool:
        p: LiquidityBosParams = self.params
        hour = ts.hour + ts.minute / 60
        return p.session_start_hour <= hour < p.session_end_hour

    # ------------------------------------------------------------ retest, confirmación y filtros

    def _entry_signal(self, setup: _Setup, since: pd.DataFrame, candles: pd.DataFrame, day_key: str) -> Signal | None:
        p: LiquidityBosParams = self.params
        bull = setup.side == "buy"
        lo, hi = setup.zone_low, setup.zone_high

        def touched(bar) -> bool:
            return bar["low"] <= hi if bull else bar["high"] >= lo

        def confirmed(bar) -> bool:
            if bull:
                ok = bar["close"] > hi
                return ok and (bar["close"] > bar["open"] or not p.require_confirmation_candle)
            ok = bar["close"] < lo
            return ok and (bar["close"] < bar["open"] or not p.require_confirmation_candle)

        def broken(bar) -> bool:
            return bar["close"] < lo if bull else bar["close"] > hi

        earlier = since.iloc[:-1]
        if any(broken(b) for _, b in earlier.iterrows()):
            return None  # el nivel roto no se sostuvo: setup invalidado
        if any(confirmed(b) for _, b in earlier.iterrows()):
            return None  # ya hubo confirmación antes: no es la primera
        if len(earlier) and not any(touched(b) for _, b in earlier.iterrows()):
            return None  # todavía no hubo retest
        last = since.iloc[-1]
        if not (touched(last) and confirmed(last)):
            return None

        if p.volume_filter_enabled:
            vols = candles["volume"].iloc[-(p.volume_period + 1):-1].to_numpy(dtype=float)
            if len(vols) < p.volume_period or vols.mean() <= 0 or last["volume"] / vols.mean() < p.minimum_rvol:
                return None
        if p.volatility_filter_enabled:
            tail = candles.iloc[-(p.atr_period * 4 + 2):]
            atr = atr_padded(tail["high"].to_numpy(float), tail["low"].to_numpy(float), tail["close"].to_numpy(float), p.atr_period)
            window = atr[-p.atr_period * 4:]
            window = window[~np.isnan(window)]
            if np.isnan(atr[-1]) or len(window) == 0 or window.mean() <= 0:
                return None
            ratio = atr[-1] / window.mean()
            if not (p.minimum_volatility <= ratio <= p.maximum_volatility):
                return None

        entry = float(last["close"])
        buf = p.stop_buffer_pct / 100
        stop = setup.stop_base * (1 - buf) if bull else setup.stop_base * (1 + buf)
        risk = entry - stop if bull else stop - entry
        if risk <= 0:
            return None
        beyond = [t for t in setup.targets if (t - entry >= p.minimum_risk_reward * risk if bull else entry - t >= p.minimum_risk_reward * risk)]
        if not beyond:
            return None  # el espacio hasta la liquidez no justifica el riesgo
        target = min(beyond) if bull else max(beyond)
        return Signal(
            action=setup.side,
            reason=f"{setup.reason} · retest de {setup.level:.2f} · confirmación",
            stop_loss=stop,
            take_profit=target,
            risk_pct=p.risk_pct,
        )

    # ------------------------------------------------------------ cálculo por vela de estructura (cacheado)

    def _setups_for(self, candles: pd.DataFrame, base: pd.Timedelta, structure: pd.Timedelta) -> list[_Setup]:
        boundary = closed_bar_boundary(candles.index[-1], structure, base)
        key = int(boundary.value)
        if self._struct_cache is not None and self._struct_cache[0] == key:
            return self._struct_cache[1]
        p: LiquidityBosParams = self.params
        n_bars = max(p.swing_lookback + p.mss_lookback + p.sweep_confirmation_bars + 2 * p.swing_n + 20, 4 * 96)
        bars = resample_closed_bars(candles, structure, boundary, n_bars)
        setups = self._compute_setups(candles, bars, boundary, base, structure)
        self._struct_cache = (key, setups)
        return setups

    def _regime_for(self, candles: pd.DataFrame, base: pd.Timedelta, context: pd.Timedelta) -> int:
        boundary = closed_bar_boundary(candles.index[-1], context, base)
        key = int(boundary.value)
        if self._regime_cache is not None and self._regime_cache[0] == key:
            return self._regime_cache[1]
        p: LiquidityBosParams = self.params
        bars = resample_closed_bars(candles, context, boundary, 80)
        regime = market_regime(bars["high"].to_numpy(float), bars["low"].to_numpy(float), p.swing_n) if len(bars) else 0
        self._regime_cache = (key, regime)
        return regime

    def _prev_day(self, candles: pd.DataFrame, day: pd.Timestamp) -> tuple[float, float] | None:
        key = int(day.value)
        if key not in self._day_cache:
            prev = candles.loc[(candles.index >= day - pd.Timedelta("1D")) & (candles.index < day)]
            if len(prev) == 0:
                return None
            self._day_cache[key] = (float(prev["high"].max()), float(prev["low"].min()))
            if len(self._day_cache) > 10:
                self._day_cache.pop(next(iter(self._day_cache)))
        return self._day_cache[key]

    def _compute_setups(self, candles, bars: pd.DataFrame, boundary, base, structure) -> list[_Setup]:
        p: LiquidityBosParams = self.params
        if len(bars) < 2 * p.swing_n + 5:
            return []
        o, h, l, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        t = len(c) - 1
        day = boundary.floor("1D")
        today_mask = bars.index >= day
        today_h = h[today_mask]
        today_l = l[today_mask]
        prev = self._prev_day(candles, day) if p.use_previous_day_levels else None
        setups: list[_Setup] = []
        for bull in (True, False):
            s = self._side(bull, bars.index, structure, base, o, h, l, c, t, prev, today_mask, today_h, today_l)
            if s is not None:
                setups.append(s)
        setups.sort(key=lambda x: x.formed_at, reverse=True)
        return setups

    def _side(self, bull, index, structure, base, o, h, l, c, t, prev, today_mask, today_h, today_l) -> _Setup | None:
        """Mismo espacio transformado que sweep_mss: el lado bajista se calcula invirtiendo los precios."""
        p: LiquidityBosParams = self.params
        sg = 1.0 if bull else -1.0
        C = sg * c
        O = sg * o
        H = h if bull else -l
        L = l if bull else -h
        n = p.swing_n
        hi_idx, lo_idx = swing_indices(H, L, n)
        hi_idx, lo_idx = [int(i) for i in hi_idx], [int(i) for i in lo_idx]

        # liquidez del lado que se barre (espacio transformado: siempre "por debajo")
        levels: list[tuple[float, int, int, str]] = []
        if p.use_swing_levels:
            levels += [(float(L[i]), i, i + n, "swing") for i in lo_idx]
        if p.use_equal_levels and len(lo_idx) >= 2:
            ref = abs(float(C[t])) or 1.0
            for a, b in zip(lo_idx, lo_idx[1:]):
                if abs(L[a] - L[b]) / ref * 100 <= p.liquidity_tolerance_pct:
                    levels.append((float(min(L[a], L[b])), a, b + n, "mínimos iguales"))
        if prev is not None:
            levels.append((float(prev[1] if bull else -prev[0]), -1, -1, "mínimo del día anterior" if bull else "máximo del día anterior"))

        # liquidez opuesta en precio real, para el objetivo
        targets: list[float] = []
        if p.use_swing_levels:
            targets += [float(h[i] if bull else l[i]) for i in hi_idx]  # en el espacio transformado, hi_idx es liquidez opuesta
        if prev is not None:
            targets.append(float(prev[0] if bull else prev[1]))
        if p.use_session_levels and len(today_h):
            targets.append(float(today_h.max() if bull else today_l.min()))

        # nivel de sesión: el mínimo (transformado) de hoy hasta cada vela
        session_t = None
        if p.use_session_levels:
            idx_today = np.nonzero(today_mask)[0]
            if len(idx_today):
                base_arr = L[idx_today]
                running = np.minimum.accumulate(base_arr)
                session_t = (idx_today, running)

        def session_level_before(s: int):
            if session_t is None:
                return None
            idx_today, running = session_t
            k = np.searchsorted(idx_today, s, side="left") - 1
            if k < 0:
                return None
            return float(running[k])

        for s in range(t, max(-1, t - (p.mss_lookback + p.sweep_confirmation_bars + 10)), -1):
            candidates = list(levels)
            sess = session_level_before(s)
            if sess is not None:
                candidates.append((sess, -1, -1, "mínimo de la sesión" if bull else "máximo de la sesión"))
            for level, src, formed, text in candidates:
                if formed >= s or (src >= 0 and src < s - p.swing_lookback):
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
                bos_level = H[max(prior_highs)]
                if bos_level <= level:
                    continue  # el swing que se rompe tiene que estar por encima del nivel barrido
                min_dist = abs(bos_level) * p.mss_min_break_pct / 100
                brk = next((b for b in range(reclaim + 1, min(s + p.mss_lookback, t) + 1) if C[b] > bos_level + min_dist), None)
                if brk is None:
                    continue
                band = abs(bos_level) * p.retest_tolerance_pct / 100
                zone_lo_t, zone_hi_t = bos_level - band, bos_level + band
                stop_ext_t = float(np.min(L[s:reclaim + 1]))
                stop_base = stop_ext_t if bull else -stop_ext_t
                if bull:
                    zone_low, zone_high = zone_lo_t, zone_hi_t
                    level_price = bos_level
                else:
                    zone_low, zone_high = -zone_hi_t, -zone_lo_t
                    level_price = -bos_level
                reason = f"{'LONG' if bull else 'SHORT'}: barrida de {text} {abs(level):.2f} · BOS {abs(level_price):.2f}"
                return _Setup(
                    side="buy" if bull else "sell",
                    formed_at=index[brk] + structure,
                    level=float(level_price),
                    zone_low=float(zone_low),
                    zone_high=float(zone_high),
                    stop_base=float(stop_base),
                    targets=tuple(targets),
                    reason=reason,
                )
        return None
