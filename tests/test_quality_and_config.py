import datetime as dt

import numpy as np
import pandas as pd

from app.backtest.config import BacktestConfig, ExecutionConfig, RiskConfig, ValidationConfig
from app.backtest.quality import assess_candles, sanitize_candles

T0 = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)


def _df(n=10, skip=()) -> pd.DataFrame:
    idx = [T0 + dt.timedelta(hours=i) for i in range(n) if i not in skip]
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1.0},
        index=pd.DatetimeIndex(idx, name="timestamp"),
    )


def test_sanitize_drops_invalid_rows_and_duplicates() -> None:
    df = _df()
    df.iloc[2, df.columns.get_loc("close")] = np.nan        # NaN
    df.iloc[3, df.columns.get_loc("open")] = -1.0            # precio no positivo
    df.iloc[4, df.columns.get_loc("high")] = 98.0            # high < low
    df = pd.concat([df, df.iloc[[7]]]).sort_index()          # duplicada
    clean, invalid, duplicates = sanitize_candles(df)
    assert invalid == 3 and duplicates == 1 and len(clean) == 10 - 3
    assert clean.index.is_unique and clean.index.is_monotonic_increasing


def test_assess_detects_missing_bars() -> None:
    report = assess_candles(_df(20, skip={5, 6, 7}), "60")
    assert report.total_bars == 17 and report.expected_bars == 20
    assert report.missing_bars == 3 and report.largest_gap_bars == 3
    assert any("Faltan" in w for w in report.warnings)


def test_assess_complete_data_has_no_gap_warning() -> None:
    report = assess_candles(_df(200), "60")
    assert report.missing_bars == 0 and report.warnings == []


def test_assess_empty_and_tiny_datasets_warn() -> None:
    assert assess_candles(pd.DataFrame(), "60").warnings
    assert any("muy pocas" in w for w in assess_candles(_df(10), "60").warnings)


def test_config_roundtrip_preserves_every_group() -> None:
    cfg = BacktestConfig(
        symbol="ETHUSDT", timeframe="15", days=120, initial_capital=2500.0,
        start=T0, end=T0 + dt.timedelta(days=120),
        execution=ExecutionConfig(order_type="limit", slippage_bps=3, funding_mode="constant"),
        risk=RiskConfig(sizing_mode="fixed_notional_pct", max_leverage=5, risk_per_trade_pct=0.5),
        validation=ValidationConfig(mc_method="block_bootstrap", mc_seed=42, mc_block_size=4),
    )
    restored = BacktestConfig.from_dict(cfg.to_dict())
    assert restored == cfg


def test_config_validation_reports_every_problem() -> None:
    cfg = BacktestConfig(
        initial_capital=-5, execution=ExecutionConfig(taker_fee_pct=-1, order_type="stop"),
        risk=RiskConfig(max_leverage=0), validation=ValidationConfig(mc_sims=1),
    )
    errors = " ".join(cfg.validate())
    for expected in ("capital", "comisiones", "Tipo de orden", "apalancamiento", "simulaciones"):
        assert expected in errors
