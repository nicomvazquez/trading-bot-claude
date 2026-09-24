import datetime as dt
from dataclasses import dataclass

import pytest

from app.live.stats import equity_curve, summarize

NOW = dt.datetime(2026, 9, 23, 15, 0, tzinfo=dt.timezone.utc)


@dataclass
class T:
    pnl: float | None
    closed_at: dt.datetime | None


def test_equity_curve_accumulates_closed_trades_in_time_order() -> None:
    trades = [
        T(-2.0, NOW - dt.timedelta(hours=1)),
        T(5.0, NOW - dt.timedelta(hours=3)),
        T(None, None),  # abierto: no entra
    ]
    curve = equity_curve(trades, 100.0)
    assert [round(e, 2) for _, e in curve] == [105.0, 103.0]
    assert curve[0][0] < curve[1][0]


def test_summarize_counts_and_ratios() -> None:
    trades = [
        T(6.0, NOW - dt.timedelta(hours=2)),
        T(-3.0, NOW - dt.timedelta(days=2)),
        T(3.0, NOW - dt.timedelta(days=3)),
        T(None, None),
    ]
    s = summarize(trades, now=NOW)
    assert s["open"] == 1 and s["closed"] == 3
    assert s["total_pnl"] == pytest.approx(6.0)
    assert s["pnl_today"] == pytest.approx(6.0)  # solo el de hoy (UTC)
    assert s["win_rate_pct"] == pytest.approx(200 / 3)
    assert s["profit_factor"] == pytest.approx(3.0)  # 9 / 3


def test_summarize_without_trades_has_no_ratios() -> None:
    s = summarize([], now=NOW)
    assert s["closed"] == 0 and s["total_pnl"] == 0
    assert s["win_rate_pct"] is None and s["profit_factor"] is None
