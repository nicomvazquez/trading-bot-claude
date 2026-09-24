"""Zona horaria de la aplicacion. Todo se GUARDA en UTC (base de datos, velas, resultados) y se MUESTRA en la hora
local configurada (por defecto la de Argentina, UTC-3, sin horario de verano). Este es el unico lugar que convierte."""

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import settings

UTC = dt.timezone.utc
DATE_TIME = "%d/%m/%Y %H:%M"
SHORT = "%d/%m %H:%M"
DATE = "%d/%m/%Y"


def tz() -> ZoneInfo:
    return ZoneInfo(settings.app_timezone)


def to_local(value) -> dt.datetime | None:
    """Instante en la hora local (con zona). Un valor sin zona se toma como UTC, que es como se guarda todo."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz())


def naive_local(value) -> dt.datetime | None:
    """Hora local sin zona: lo que necesitan los graficos (Plotly muestra el reloj tal cual y descartaria un offset)
    y las planillas (Excel no admite fechas con zona)."""
    local = to_local(value)
    return None if local is None else local.replace(tzinfo=None)


def local_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(tz()).tz_localize(None)


def fmt(value, pattern: str = DATE_TIME) -> str:
    local = to_local(value)
    return "—" if local is None else local.strftime(pattern)


def local_today(now: dt.datetime | None = None) -> dt.date:
    return to_local(now or dt.datetime.now(UTC)).date()


def local_midnight_utc(day: dt.date) -> dt.datetime:
    """El instante UTC en que empieza `day` en la hora local (00:00 de Argentina = 03:00 UTC)."""
    return dt.datetime.combine(day, dt.time.min, tzinfo=tz()).astimezone(UTC)


def day_start_utc(now: dt.datetime | None = None) -> dt.datetime:
    """Comienzo del dia local actual, como instante UTC. Es el limite de «hoy» para el PnL diario y el limite de perdida."""
    return local_midnight_utc(local_today(now))


def label() -> str:
    """Nombre corto de la zona para mostrar junto a las horas, p. ej. «hora de Argentina (UTC-3)»."""
    offset = to_local(dt.datetime.now(UTC)).utcoffset()
    hours = offset.total_seconds() / 3600
    sign = "+" if hours >= 0 else "-"
    amount = f"{abs(hours):g}"
    name = "Argentina" if settings.app_timezone.startswith("America/Argentina") else settings.app_timezone.split("/")[-1].replace("_", " ")
    return f"hora de {name} (UTC{sign}{amount})"
