"""Indicadores en numpy, pensados para calcularse sobre una ventana corta de velas
(las estrategias solo necesitan las ultimas N velas, no todo el historial)."""

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Rango verdadero de cada vela salvo la primera (necesita el cierre anterior)."""
    prev_close = close[:-1]
    return np.maximum.reduce([high[1:] - low[1:], np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)])


def atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """ATR como media simple del rango verdadero. Longitud: len(close) - period."""
    tr = true_range(high, low, close)
    if len(tr) < period:
        return np.empty(0)
    return np.convolve(tr, np.ones(period) / period, mode="valid")


def rolling_mean_std(values: np.ndarray, period: int) -> tuple[np.ndarray, np.ndarray]:
    """Media y desvio (poblacional) moviles. Longitud: len(values) - period + 1."""
    windows = sliding_window_view(values, period)
    return windows.mean(axis=1), windows.std(axis=1)


def rsi_wilder(closes: np.ndarray, period: int) -> np.ndarray:
    """RSI de Wilder. Devuelve un valor por cada cierre desde el indice `period`.
    Converge rapido: alcanza con unas pocas decenas de velas de calentamiento."""
    if len(closes) <= period:
        return np.empty(0)
    delta = np.diff(closes)
    gains, losses = np.clip(delta, 0, None), np.clip(-delta, 0, None)
    avg_gain, avg_loss = gains[:period].mean(), losses[:period].mean()
    out = np.empty(len(delta) - period + 1)
    out[0] = _rsi_value(avg_gain, avg_loss)
    for i, (g, l) in enumerate(zip(gains[period:], losses[period:]), start=1):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def swing_indices(highs: np.ndarray, lows: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Indices de swing highs / swing lows confirmados: extremo estricto (sin empates) frente a n velas a
    cada lado. Un swing en i solo existe para quien mira desde i + n en adelante (sin look-ahead)."""
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


def atr_padded(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> np.ndarray:
    """ATR (media simple del rango verdadero) con la misma longitud que las velas; NaN antes de `period` velas."""
    prev_close = np.concatenate(([closes[0]], closes[:-1]))
    tr = np.maximum.reduce([highs - lows, np.abs(highs - prev_close), np.abs(lows - prev_close)])
    atr = np.full(len(tr), np.nan)
    if len(tr) >= period:
        cs = np.concatenate(([0.0], np.cumsum(tr)))
        atr[period - 1:] = (cs[period:] - cs[:-period]) / period
    return atr


def market_regime(highs: np.ndarray, lows: np.ndarray, n: int) -> int:
    """+1 tendencia alcista (HH + HL), -1 bajista (LH + LL), 0 rango o sin datos suficientes."""
    hi, lo = swing_indices(highs, lows, n)
    if len(hi) < 2 or len(lo) < 2:
        return 0
    up_h, up_l = highs[hi[-1]] > highs[hi[-2]], lows[lo[-1]] > lows[lo[-2]]
    dn_h, dn_l = highs[hi[-1]] < highs[hi[-2]], lows[lo[-1]] < lows[lo[-2]]
    if up_h and up_l:
        return 1
    if dn_h and dn_l:
        return -1
    return 0


def closed_bar_boundary(last_open: pd.Timestamp, bar: pd.Timedelta, base: pd.Timedelta) -> pd.Timestamp:
    """Inicio de la primera barra de `bar` minutos que todavía no está completa (todo lo anterior está cerrado)."""
    bar_start = last_open.floor(bar)
    return bar_start + bar if last_open + base >= bar_start + bar else bar_start


def resample_closed_bars(candles: pd.DataFrame, bar: pd.Timedelta, boundary: pd.Timestamp, n_bars: int) -> pd.DataFrame:
    """Barras de `bar` minutos formadas solo con velas anteriores a `boundary` (ya cerradas)."""
    start = boundary - bar * n_bars
    sub = candles.loc[(candles.index >= start) & (candles.index < boundary)]
    out = sub.resample(bar, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["close"])


def adx_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ADX de Wilder (suavizado exponencial con alfa 1/period). Cada valor usa solo velas anteriores o iguales."""
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=alpha, adjust=False).mean()
