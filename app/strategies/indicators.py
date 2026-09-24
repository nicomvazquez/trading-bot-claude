"""Indicadores en numpy, pensados para calcularse sobre una ventana corta de velas
(las estrategias solo necesitan las ultimas N velas, no todo el historial)."""

import numpy as np
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
