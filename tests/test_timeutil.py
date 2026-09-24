import datetime as dt

import pandas as pd
import pytest

from app import timeutil
from app.config import settings

UTC = dt.timezone.utc


def test_argentina_is_utc_minus_3_all_year() -> None:
    assert settings.app_timezone == "America/Argentina/Buenos_Aires"
    for month in (1, 7):  # sin horario de verano
        local = timeutil.to_local(dt.datetime(2026, month, 15, 12, 0, tzinfo=UTC))
        assert local.hour == 9 and local.utcoffset() == dt.timedelta(hours=-3)


def test_conversion_accepts_naive_utc_timestamps_and_none() -> None:
    assert timeutil.to_local(dt.datetime(2026, 9, 24, 2, 30)).day == 23          # sin zona: se toma como UTC
    assert timeutil.fmt(pd.Timestamp("2026-09-24 02:30", tz="UTC")) == "23/09/2026 23:30"
    assert timeutil.fmt(pd.Timestamp("2026-09-24 02:30", tz="UTC"), timeutil.SHORT) == "23/09 23:30"
    assert timeutil.fmt(None) == "—" and timeutil.to_local(None) is None and timeutil.naive_local(float("nan")) is None


def test_naive_local_and_index_conversion_drop_the_zone_but_keep_the_wall_clock() -> None:
    naive = timeutil.naive_local(dt.datetime(2026, 9, 24, 3, 0, tzinfo=UTC))
    assert naive == dt.datetime(2026, 9, 24, 0, 0) and naive.tzinfo is None
    idx = pd.date_range("2026-09-24 00:00", periods=3, freq="1h", tz="UTC")
    local = timeutil.local_index(idx)
    assert local.tz is None and local[0] == pd.Timestamp("2026-09-23 21:00")
    assert timeutil.local_index(idx.tz_localize(None))[0] == pd.Timestamp("2026-09-23 21:00")  # sin zona = UTC


def test_the_local_day_starts_at_03_00_utc() -> None:
    assert timeutil.local_midnight_utc(dt.date(2026, 9, 24)) == dt.datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    # las 01:00 UTC del 24/09 todavia son el 23/09 en Argentina: el «dia de hoy» empezo el 23 a las 03:00 UTC
    assert timeutil.day_start_utc(dt.datetime(2026, 9, 24, 1, 0, tzinfo=UTC)) == dt.datetime(2026, 9, 23, 3, 0, tzinfo=UTC)
    assert timeutil.day_start_utc(dt.datetime(2026, 9, 24, 3, 0, tzinfo=UTC)) == dt.datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    assert timeutil.day_start_utc(dt.datetime(2026, 9, 24, 15, 0, tzinfo=UTC)) == dt.datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    assert timeutil.local_today(dt.datetime(2026, 9, 24, 2, 59, tzinfo=UTC)) == dt.date(2026, 9, 23)


def test_label_names_the_zone_and_offset() -> None:
    assert timeutil.label() == "hora de Argentina (UTC-3)"


def test_a_different_configured_zone_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_timezone", "Europe/Madrid")
    assert timeutil.to_local(dt.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)).hour == 13
    assert timeutil.label().startswith("hora de Madrid (UTC+")
