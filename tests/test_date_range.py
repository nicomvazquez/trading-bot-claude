import datetime as dt

import pandas as pd
import pytest

from app.backtest.config import MAX_RANGE_DAYS, MIN_RANGE_DAYS, BacktestConfig, ConfigError
from app.backtest.service import coverage_warnings, resolve_period

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def cfg(start=None, end=None, days=90) -> BacktestConfig:
    return BacktestConfig(start=start, end=end, days=days)


def day(y, m, d) -> dt.datetime:
    return dt.datetime(y, m, d, tzinfo=UTC)


# ------------------------------------------------------------------ resolucion del periodo

def test_without_a_range_days_count_back_from_now() -> None:
    start, end = resolve_period(cfg(days=30), now=NOW)
    assert end == NOW and start == NOW - dt.timedelta(days=30)


def test_a_date_range_is_used_exactly() -> None:
    start, end = resolve_period(cfg(day(2025, 1, 1), day(2025, 7, 1)), now=NOW)
    assert (start, end) == (day(2025, 1, 1), day(2025, 7, 1))


def test_an_end_in_the_future_is_clamped_to_now() -> None:
    start, end = resolve_period(cfg(day(2026, 6, 1), day(2027, 1, 1)), now=NOW)
    assert start == day(2026, 6, 1) and end == NOW


def test_a_range_entirely_in_the_future_is_rejected_with_a_clear_message() -> None:
    with pytest.raises(ConfigError, match="futuro"):
        resolve_period(cfg(day(2027, 1, 1), day(2027, 6, 1)), now=NOW)


# ------------------------------------------------------------------ validacion

def test_validation_of_date_ranges() -> None:
    assert cfg(day(2025, 1, 1), day(2025, 7, 1)).validate() == []
    assert any("anterior" in e for e in cfg(day(2025, 7, 1), day(2025, 1, 1)).validate())
    assert any("anterior" in e for e in cfg(day(2025, 1, 1), day(2025, 1, 1)).validate())
    too_short = cfg(day(2025, 1, 1), day(2025, 1, 1) + dt.timedelta(days=MIN_RANGE_DAYS - 1))
    assert any(f"al menos {MIN_RANGE_DAYS} días" in e for e in too_short.validate())
    assert cfg(day(2025, 1, 1), day(2025, 1, 1) + dt.timedelta(days=MIN_RANGE_DAYS)).validate() == []
    too_long = cfg(day(2020, 1, 1), day(2020, 1, 1) + dt.timedelta(days=MAX_RANGE_DAYS + 1))
    assert any(f"superar {MAX_RANGE_DAYS} días" in e for e in too_long.validate())


def test_configs_without_a_range_keep_validating_as_before() -> None:
    assert cfg(days=90).validate() == [] and any("periodo" in e for e in cfg(days=0).validate())


def test_span_days_uses_the_range_when_present() -> None:
    assert cfg(day(2025, 1, 1), day(2025, 4, 11), days=999).span_days() == pytest.approx(100.0)
    assert cfg(days=45).span_days() == 45.0


def test_the_range_survives_saving_and_loading_the_configuration() -> None:
    original = cfg(day(2025, 3, 1), day(2025, 9, 1))
    back = BacktestConfig.from_dict(original.to_dict())
    assert back.start == original.start and back.end == original.end
    assert BacktestConfig.from_dict(cfg().to_dict()).start is None  # corridas viejas, sin rango


# ------------------------------------------------------------------ cobertura de datos

def candles(start: dt.datetime, hours: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=hours, freq="1h", tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)


def test_no_warning_when_the_data_covers_the_whole_range() -> None:
    c = candles(day(2026, 1, 1), 24 * 30)
    assert coverage_warnings(c, day(2026, 1, 1), day(2026, 1, 31), "60") == []


def test_warns_when_the_symbol_only_has_data_from_a_later_date() -> None:
    c = candles(day(2026, 1, 11), 24 * 20)  # el simbolo empezo a cotizar el 11/01
    warnings = coverage_warnings(c, day(2026, 1, 1), day(2026, 1, 31), "60")
    assert len(warnings) == 1 and "10/01/2026" in warnings[0] and "31/12/2025" in warnings[0]  # en hora argentina


def test_warns_when_the_data_ends_before_the_requested_end() -> None:
    c = candles(day(2026, 1, 1), 24 * 10)
    warnings = coverage_warnings(c, day(2026, 1, 1), day(2026, 1, 31), "60")
    assert len(warnings) == 1 and "terminan el 10/01/2026" in warnings[0] and "30/01/2026" in warnings[0]


def test_a_gap_of_a_couple_of_bars_at_the_edges_is_not_worth_a_warning() -> None:
    c = candles(day(2026, 1, 1) + dt.timedelta(hours=2), 24 * 30 - 4)
    assert coverage_warnings(c, day(2026, 1, 1), day(2026, 1, 31), "60") == []
    assert coverage_warnings(pd.DataFrame(), day(2026, 1, 1), day(2026, 1, 31), "60") == []
