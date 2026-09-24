"""Estadisticas de la operatoria en vivo, calculadas a partir de los trades guardados.
Funciones puras (sin base de datos) para poder testearlas."""

import datetime as dt
from typing import Iterable


def closed_trades(trades: Iterable) -> list:
    return sorted((t for t in trades if t.closed_at is not None), key=lambda t: t.closed_at)


def equity_curve(trades: Iterable, initial_capital: float) -> list[tuple[dt.datetime, float]]:
    """Capital realizado: capital inicial + PnL acumulado de los trades cerrados."""
    equity, points = initial_capital, []
    for trade in closed_trades(trades):
        equity += trade.pnl or 0.0
        points.append((trade.closed_at, equity))
    return points


def summarize(trades: Iterable, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    trades = list(trades)
    closed = closed_trades(trades)
    pnls = [t.pnl or 0.0 for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return {
        "open": len(trades) - len(closed),
        "closed": len(closed),
        "total_pnl": sum(pnls),
        "pnl_today": sum(t.pnl or 0.0 for t in closed if t.closed_at >= day_start),
        "win_rate_pct": len(wins) / len(closed) * 100 if closed else None,
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
    }
