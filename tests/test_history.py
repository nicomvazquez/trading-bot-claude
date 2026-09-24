import datetime as dt
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pytest

from app.backtest.config import BacktestConfig
from app.backtest.engine import Backtester
from app.backtest.history import (
    MAX_EQUITY_POINTS, RunView, config_diff, config_from_run, cost_summary, deserialize_equity, deserialize_trades,
    equity_pct, has_full_data, json_safe, metric_rows, result_from_run, run_view, serialize_equity, serialize_trades,
)
from app.backtest.metrics import compute_metrics
from app.strategies import registry

UTC = dt.timezone.utc


def real_run():
    rng = np.random.default_rng(4)
    n = 2500
    regime = np.repeat(rng.choice([-0.0006, 0.0, 0.0009], size=n // 250 + 1), 250)[:n]
    closes = 100 * np.exp(np.cumsum(regime + rng.normal(0, 0.004, n)))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    candles = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * 1.002, "low": np.minimum(opens, closes) * 0.998, "close": closes, "volume": 1.0}, index=idx)
    cls = registry.get("donchian_breakout")
    cfg = BacktestConfig(timeframe="60")
    result = Backtester(execution=cfg.execution, risk=cfg.risk).run(cls(cls.params_model()), candles, 1000.0)
    result.symbol, result.timeframe, result.config = "TESTUSDT", "60", cfg.to_dict()
    return result, compute_metrics(result, "60"), cfg


@dataclass
class FakeRun:
    id: int = 1
    strategy_key: str = "donchian_breakout"
    symbol: str = "BTCUSDT"
    timeframe: str = "60"
    params: dict = field(default_factory=dict)
    start_date: dt.datetime = dt.datetime(2026, 1, 1, tzinfo=UTC)
    end_date: dt.datetime = dt.datetime(2026, 4, 1, tzinfo=UTC)
    metrics: dict = field(default_factory=dict)
    strategy_version: str | None = "v1+abc"
    exchange: str | None = "bybit"
    initial_capital: float | None = 1000.0
    config: dict | None = None
    trades: list | None = None
    equity: dict | None = None


# ------------------------------------------------------------------ serializacion

def test_trades_survive_a_json_round_trip_field_by_field() -> None:
    result, _, _ = real_run()
    assert len(result.trades) > 5
    stored = json.loads(json.dumps(serialize_trades(result.trades)))  # lo que hace la base: JSON de ida y vuelta
    back = deserialize_trades(stored)
    assert len(back) == len(result.trades)
    for original, restored in zip(result.trades, back, strict=True):
        assert restored.side == original.side and restored.id == original.id
        assert restored.entry_time == original.entry_time and restored.exit_time == original.exit_time
        for name in ("entry_price", "exit_price", "qty", "pnl", "gross_pnl", "entry_fee", "exit_fee", "funding", "equity_before", "stop_loss"):
            assert getattr(restored, name) == pytest.approx(getattr(original, name))
        assert restored.exit_reason == original.exit_reason and restored.notional == pytest.approx(original.notional)
        assert restored.duration == original.duration


def test_equity_round_trip_keeps_values_and_timestamps() -> None:
    result, _, _ = real_run()
    payload = json.loads(json.dumps(serialize_equity(result.equity_curve, max_points=10_000)))
    back = deserialize_equity(payload)
    assert payload["stride"] == 1 and len(back) == len(result.equity_curve)
    assert back.index.equals(pd.DatetimeIndex(result.equity_curve.index)) and back.to_numpy() == pytest.approx(result.equity_curve.to_numpy(), abs=1e-5)


def test_long_equity_curves_are_thinned_but_keep_the_first_and_last_points() -> None:
    idx = pd.date_range("2026-01-01", periods=30_000, freq="15min", tz="UTC")
    curve = pd.Series(np.linspace(1000, 1500, len(idx)), index=idx)
    payload = serialize_equity(curve)
    assert len(payload["t"]) <= MAX_EQUITY_POINTS + 1 and payload["stride"] > 1 and payload["bars"] == 30_000
    back = deserialize_equity(payload)
    assert back.index[0] == curve.index[0] and back.index[-1] == curve.index[-1] and back.iloc[-1] == pytest.approx(1500)


def test_json_safe_removes_nan_numpy_and_datetimes() -> None:
    data = {"a": np.float64(1.5), "b": float("nan"), "c": [np.int64(3), float("inf")], "d": pd.Timestamp("2026-01-01", tz="UTC"), "e": np.bool_(True)}
    safe = json_safe(data)
    json.dumps(safe, allow_nan=False)  # no lanza: es JSON estricto
    assert safe["a"] == 1.5 and safe["b"] is None and safe["c"] == [3, None] and safe["d"].startswith("2026-01-01") and safe["e"] is True


def test_a_saved_run_rebuilds_the_result_and_its_config() -> None:
    result, metrics, cfg = real_run()
    run = FakeRun(config=json_safe(cfg.to_dict()), trades=json.loads(json.dumps(serialize_trades(result.trades))),
                  equity=json.loads(json.dumps(serialize_equity(result.equity_curve, 10_000))), metrics=json_safe(metrics), params=result.params)
    rebuilt = result_from_run(run)
    assert rebuilt is not None and len(rebuilt.trades) == len(result.trades) and rebuilt.initial_capital == 1000.0
    assert rebuilt.equity_curve.iloc[-1] == pytest.approx(result.equity_curve.iloc[-1], abs=1e-4)
    assert sum(t.pnl for t in rebuilt.trades) == pytest.approx(rebuilt.equity_curve.iloc[-1] - rebuilt.initial_capital, abs=1e-3)  # las cuentas cierran
    cloned = config_from_run(run)
    assert has_full_data(run) and cloned.execution == cfg.execution and cloned.risk == cfg.risk and cloned.validation == cfg.validation
    # una corrida hecha con "N dias hacia atras" se clona con las fechas exactas que abarco
    assert cloned.start == run.start_date and cloned.end == run.end_date
    # si ya tenia un rango de fechas, se respeta tal cual
    ranged = json_safe(cfg.to_dict()) | {"start": "2025-03-01T00:00:00+00:00", "end": "2025-06-01T00:00:00+00:00"}
    again = config_from_run(FakeRun(config=ranged))
    assert again.start == dt.datetime(2025, 3, 1, tzinfo=UTC) and again.end == dt.datetime(2025, 6, 1, tzinfo=UTC)
    # las metricas de operaciones recalculadas desde las reconstruidas coinciden con las guardadas
    from app.backtest.metrics import trade_summary
    assert trade_summary(rebuilt.trades)["num_trades"] == metrics["num_trades"]


def test_an_old_run_without_full_data_can_still_be_cloned_with_what_it_has() -> None:
    old = FakeRun(config=None, trades=None, equity=None, strategy_version=None, initial_capital=None)
    assert not has_full_data(old) and result_from_run(old) is None
    cfg = config_from_run(old)
    assert cfg.symbol == "BTCUSDT" and cfg.timeframe == "60" and cfg.days == 90  # enero -> abril
    assert cfg.start == old.start_date and cfg.end == old.end_date


# ------------------------------------------------------------------ comparacion

def views() -> list[RunView]:
    base = BacktestConfig(timeframe="60")
    costly = BacktestConfig(timeframe="60")
    costly.execution.slippage_bps = 5.0
    a = FakeRun(id=1, config=json_safe(base.to_dict()), params={"entry_bars": 20, "risk_pct": 1.0}, metrics={"total_return_pct": 10.0, "sharpe_ratio": 1.0})
    b = FakeRun(id=2, config=json_safe(costly.to_dict()), params={"entry_bars": 40, "risk_pct": 1.0}, metrics={"total_return_pct": 4.0})
    return [run_view(a, "Corrida 1"), run_view(b, "Corrida 2")]


def test_config_diff_shows_only_what_differs_with_readable_labels() -> None:
    rows = {r["key"]: r for r in config_diff(views())}
    assert set(rows) == {"execution.slippage_bps", "params.entry_bars"}       # lo igual (fee, capital, riesgo...) no aparece
    assert rows["execution.slippage_bps"]["label"] == "Slippage (bps)" and rows["execution.slippage_bps"]["values"] == [0.0, 5.0]
    assert rows["params.entry_bars"]["label"] == "Parámetro · entry_bars" and rows["params.entry_bars"]["values"] == [20, 40]


def test_config_diff_orders_general_settings_before_parameters_and_handles_missing_values() -> None:
    v = views()
    v[1].flat["symbol"] = "ETHUSDT"
    del v[1].flat["params.risk_pct"]                                          # una corrida sin ese parametro
    keys = [r["key"] for r in config_diff(v)]
    assert keys.index("symbol") < keys.index("execution.slippage_bps") < keys.index("params.entry_bars")
    assert {r["key"]: r for r in config_diff(v)}["params.risk_pct"]["values"] == [1.0, None]
    assert config_diff([views()[0], views()[0]]) == []                        # dos corridas identicas: sin diferencias


def test_metric_rows_keep_the_selection_order_and_never_sort_by_performance() -> None:
    v = views()
    rows = {r["key"]: r for r in metric_rows(v, {"Performance": ["total_return_pct"], "Risk": ["sharpe_ratio", "calmar_ratio"]}, {"total_return_pct": "Retorno"})}
    assert rows["total_return_pct"]["values"] == [10.0, 4.0] and rows["sharpe_ratio"]["values"] == [1.0, None]
    assert "calmar_ratio" not in rows                                         # ninguna corrida lo tiene: no se muestra
    rows_reversed = {r["key"]: r for r in metric_rows(v[::-1], {"Performance": ["total_return_pct"]}, {})}
    assert rows_reversed["total_return_pct"]["values"] == [4.0, 10.0]         # el orden lo manda quien elige


def test_equity_pct_normalizes_runs_with_different_capital() -> None:
    a = pd.Series([1000.0, 1100.0]); b = pd.Series([5000.0, 5500.0])
    assert equity_pct(a, 1000.0).tolist() == pytest.approx([0.0, 10.0]) and equity_pct(b, 5000.0).tolist() == pytest.approx([0.0, 10.0])


def test_cost_summary_is_short_and_readable() -> None:
    cfg = BacktestConfig()
    cfg.execution.slippage_bps, cfg.execution.funding_mode = 5.0, "historical"
    assert cost_summary(cfg) == "fee 0.055% · slip 5 bps · funding hist."


def test_history_ui_shows_settings_in_plain_spanish() -> None:
    from app.ui.backtest_history import _value

    assert _value("execution.funding_mode", "historical") == "Histórico real" and _value("execution.order_type", "limit") == "Limit (maker)"
    assert _value("timeframe", "240") == "4 horas" and _value("execution.intrabar_resolution", True) == "Sí"
    assert _value("execution.maker_fee_pct", None) == "—" and _value("execution.slippage_bps", 5.0) == "5" and _value("params.entry_bars", 40) == "40"
    assert _value("strategy_key", "donchian_breakout") == "Ruptura de canal (Donchian)"
