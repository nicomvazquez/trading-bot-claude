import datetime as dt

import pandas as pd

from app.backtest.engine import BacktestResult, TradeRecord
from app.backtest.optimizer import expand_range
from app.ui.backtest_charts import equity_drawdown_chart
from app.ui.backtest_format import fmt_pct, fmt_usd, format_metric, sign_class


def _result() -> BacktestResult:
    index = pd.DatetimeIndex(
        [dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(hours=i) for i in range(10)]
    )
    equity = pd.Series([1000, 1010, 1005, 1020, 990, 1000, 1030, 1025, 1040, 1050], index=index, dtype=float)
    win = TradeRecord(
        id=1, side="long", entry_time=index[1], entry_price=100.0, qty=1.0, equity_before=1000.0,
        exit_time=index[3], exit_price=110.0, pnl=10.0, pnl_pct=0.01, exit_reason="take_profit",
    )
    loss = TradeRecord(
        id=2, side="short", entry_time=index[3], entry_price=110.0, qty=1.0, equity_before=1020.0,
        exit_time=index[4], exit_price=120.0, pnl=-10.0, pnl_pct=-0.01, exit_reason="stop_loss",
    )
    return BacktestResult("s", "BTCUSDT", "60", {}, 1000.0, equity, [win, loss])


def test_equity_chart_uses_two_panels_with_independent_axes() -> None:
    fig = equity_drawdown_chart(_result())

    assert fig.layout.yaxis.domain != fig.layout.yaxis2.domain
    assert fig.layout.yaxis2.overlaying is None  # nada de doble eje superpuesto
    assert fig.layout.xaxis.matches == "x2"  # los dos paneles comparten el mismo eje de tiempo


def test_equity_chart_marks_entries_by_side_and_exits_by_outcome_with_distinct_symbols() -> None:
    fig = equity_drawdown_chart(_result())

    symbols = {t.name: t.marker.symbol for t in fig.data if t.mode == "markers"}
    assert symbols == {
        "Entrada Long": "triangle-up", "Entrada Short": "triangle-down",
        "Salida ganadora": "circle", "Salida perdedora": "x",
    }


def test_chart_markers_carry_the_trade_id_so_they_can_be_selected() -> None:
    fig = equity_drawdown_chart(_result())

    ids = {t.name: list(t.customdata) for t in fig.data if t.mode == "markers"}
    assert ids["Entrada Long"] == [1] and ids["Salida perdedora"] == [2]


def test_highlighting_a_trade_adds_a_band_between_entry_and_exit() -> None:
    result = _result()
    fig = equity_drawdown_chart(result, highlight=result.trades[0])
    assert len(fig.layout.shapes) >= 1


def test_expand_range_generates_values_and_keeps_fixed_value_when_min_equals_max() -> None:
    assert expand_range(10, 20, 5, is_int=True) == [10, 15, 20]
    assert expand_range(20, 20, 1, is_int=True) == [20]  # queda fijo en 20, no en el default
    assert expand_range(0.5, 1.0, 0.25, is_int=False) == [0.5, 0.75, 1.0]


def test_expand_range_handles_invalid_input() -> None:
    assert expand_range(None, 10, 1, is_int=True) == []
    assert expand_range(10, 5, 1, is_int=True) == [10]
    assert expand_range(1, 5, 0, is_int=True) == [1]
    assert expand_range(1, 2, 0.5, is_int=True) == [1, 2]  # sin duplicados al redondear


def test_formatters() -> None:
    assert fmt_usd(1234.5) == "$1,234.50"
    assert fmt_usd(-5.1, signed=True) == "-$5.10"
    assert fmt_usd(3.0, signed=True) == "+$3.00"
    assert fmt_pct(3.456, signed=True) == "+3.46%"
    assert fmt_pct(-2.0) == "-2.00%"
    assert format_metric("profit_factor", None) == "—"
    assert format_metric("profit_factor", None, {"num_trades": 3, "avg_loss": None}) == "∞ (sin pérdidas)"
    assert format_metric("max_drawdown_duration_days", 16) == "16.0 días"
    assert sign_class(0) == ""


def test_default_sensitivity_range_is_centered_on_the_current_value_and_respects_bounds() -> None:
    from app.backtest.robustness import numeric_params
    from app.strategies import registry
    from app.ui.backtest_robustness import default_range, within_bounds

    fields = numeric_params(registry.get("sma_cross"))
    assert default_range(fields["fast_period"], 10) == (7, 13, 1)  # 7 puntos: 7..13
    assert default_range(fields["slow_period"], 50) == (35, 65, 5)  # paso ~10% del valor
    assert default_range(fields["fast_period"], 2)[0] == 2  # no baja del minimo permitido (>= 2)
    lo, hi, step = default_range(fields["risk_pct"], 1.0)
    assert (lo, hi, step) == (0.7, 1.3, 0.1)
    assert within_bounds([1, 2, 3, 250], fields["fast_period"]) == [2, 3]


def test_window_estimate_is_conservative_about_the_real_history_length() -> None:
    from app.ui.backtest_validation import _estimate_windows

    assert _estimate_windows(90, 45, 15, 15) == 2  # la historia real cubre un poco menos de 90 dias
    assert _estimate_windows(100, 45, 15, 15) == 3
    assert _estimate_windows(60, 45, 15, 15) == 0
    assert _estimate_windows(90, 0, 15, 15) == 0
