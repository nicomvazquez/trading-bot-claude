"""Validacion y auditoria de calidad de las velas antes de simular."""

import datetime as dt
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

BAR_SECONDS = {
    "1": 60, "3": 180, "5": 300, "15": 900, "30": 1800,
    "60": 3600, "120": 7200, "240": 14400, "360": 21600, "720": 43200,
    "D": 86400, "W": 604800,
}
# Un ano de 24/7 (cripto no cierra) en cantidad de velas por timeframe.
PERIODS_PER_YEAR = {tf: round(365 * 86400 / s) for tf, s in BAR_SECONDS.items()}


def bar_delta(timeframe: str) -> pd.Timedelta:
    return pd.Timedelta(seconds=BAR_SECONDS.get(timeframe, 3600))


@dataclass
class DataQualityReport:
    total_bars: int = 0
    expected_bars: int = 0
    missing_bars: int = 0
    missing_pct: float = 0.0
    largest_gap_bars: int = 0
    invalid_rows_removed: int = 0
    duplicates_removed: int = 0
    first: str | None = None
    last: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def sanitize_candles(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Ordena, elimina duplicados y filas invalidas (NaN, precios <= 0, OHLC
    incoherente). Devuelve (df limpio, invalidas eliminadas, duplicadas eliminadas)."""
    if df.empty:
        return df, 0, 0
    df = df.sort_index()
    duplicated = df.index.duplicated(keep="last")
    duplicates = int(duplicated.sum())
    df = df[~duplicated]

    ohlc = df[["open", "high", "low", "close"]]
    tol = 1e-9
    valid = (
        ohlc.notna().all(axis=1)
        & (ohlc > 0).all(axis=1)
        & (df["high"] + tol >= df["low"])
        & (df["high"] + tol >= df[["open", "close"]].max(axis=1))
        & (df["low"] - tol <= df[["open", "close"]].min(axis=1))
    )
    invalid = int((~valid).sum())
    return df[valid], invalid, duplicates


def assess_candles(df: pd.DataFrame, timeframe: str, invalid_removed: int = 0, duplicates_removed: int = 0) -> DataQualityReport:
    report = DataQualityReport(invalid_rows_removed=invalid_removed, duplicates_removed=duplicates_removed)
    report.total_bars = len(df)
    if df.empty:
        report.warnings.append("No hay velas para el simbolo y rango pedidos.")
        return report

    delta = bar_delta(timeframe)
    report.first, report.last = df.index[0].isoformat(), df.index[-1].isoformat()
    span = df.index[-1] - df.index[0]
    report.expected_bars = int(round(span / delta)) + 1
    report.missing_bars = max(report.expected_bars - report.total_bars, 0)
    report.missing_pct = report.missing_bars / report.expected_bars * 100 if report.expected_bars else 0.0
    if len(df) > 1:
        gaps = np.diff(df.index.values).astype("timedelta64[s]").astype(float) / delta.total_seconds()
        report.largest_gap_bars = max(int(round(gaps.max())) - 1, 0)

    if report.missing_bars:
        level = "Faltan" if report.missing_pct >= 0.5 else "Faltan (pocas)"
        report.warnings.append(
            f"{level} {report.missing_bars} velas ({report.missing_pct:.2f}%); el hueco mas largo es de "
            f"{report.largest_gap_bars} velas. Cripto opera 24/7: puede ser un dato faltante del exchange."
        )
    if invalid_removed:
        report.warnings.append(f"Se descartaron {invalid_removed} velas invalidas (NaN, precios no positivos o OHLC incoherente).")
    if duplicates_removed:
        report.warnings.append(f"Se descartaron {duplicates_removed} velas duplicadas.")
    if report.total_bars < 100:
        report.warnings.append(f"Solo hay {report.total_bars} velas: muy pocas para una simulacion confiable.")
    return report
