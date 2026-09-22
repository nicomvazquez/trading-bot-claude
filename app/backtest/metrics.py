import numpy as np
import pandas as pd

from app.backtest.engine import BacktestResult

_PERIODS_PER_YEAR = {
    "1": 525_600, "3": 175_200, "5": 105_120, "15": 35_040, "30": 17_520,
    "60": 8_760, "120": 4_380, "240": 2_190, "360": 1_460, "720": 730,
    "D": 365, "W": 52,
}


def compute_metrics(result: BacktestResult, timeframe: str) -> dict:
    equity = result.equity_curve
    trades = result.trades
    initial = result.initial_capital

    if equity.empty:
        return {"error": "No hay suficientes velas para simular (aumenta el rango de fechas)."}

    final_equity = float(equity.iloc[-1])
    total_return_pct = (final_equity / initial - 1) * 100

    n_days = max((equity.index[-1] - equity.index[0]).total_seconds() / 86400, 1)
    cagr_pct = ((final_equity / initial) ** (365 / n_days) - 1) * 100 if final_equity > 0 else -100.0

    running_max = equity.cummax()
    drawdown_pct = (equity - running_max) / running_max * 100
    max_drawdown_pct = float(drawdown_pct.min())
    max_dd_duration_days = _max_drawdown_duration_days(drawdown_pct)

    returns = equity.pct_change().dropna()
    periods_per_year = _PERIODS_PER_YEAR.get(timeframe, 8_760)
    sharpe_ratio = _annualized_ratio(returns, periods_per_year, downside_only=False)
    sortino_ratio = _annualized_ratio(returns, periods_per_year, downside_only=True)
    calmar_ratio = (cagr_pct / abs(max_drawdown_pct)) if max_drawdown_pct != 0 else None

    closed = [t for t in trades if t.pnl is not None]
    wins = [t.pnl for t in closed if t.pnl > 0]
    losses = [t.pnl for t in closed if t.pnl <= 0]
    win_rate_pct = (len(wins) / len(closed) * 100) if closed else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    expectancy = (win_rate_pct / 100 * avg_win) + ((1 - win_rate_pct / 100) * avg_loss)

    exposure_pct = _exposure_pct(closed, equity)

    return {
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "cagr_pct": round(cagr_pct, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "max_drawdown_duration_days": max_dd_duration_days,
        "sharpe_ratio": round(sharpe_ratio, 2),
        "sortino_ratio": round(sortino_ratio, 2),
        "calmar_ratio": round(calmar_ratio, 2) if calmar_ratio is not None else None,
        "num_trades": len(closed),
        "win_rate_pct": round(win_rate_pct, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "exposure_pct": round(exposure_pct, 2),
    }


def _annualized_ratio(returns: pd.Series, periods_per_year: int, downside_only: bool) -> float:
    if returns.empty:
        return 0.0
    denom_series = returns[returns < 0] if downside_only else returns
    std = denom_series.std()
    if not std or np.isnan(std) or std == 0:
        return 0.0
    return float(returns.mean() / std * np.sqrt(periods_per_year))


def _max_drawdown_duration_days(drawdown_pct: pd.Series) -> int:
    in_dd = drawdown_pct < 0
    if not in_dd.any():
        return 0
    groups = (~in_dd).cumsum()
    max_days = 0
    for _, segment in drawdown_pct[in_dd].groupby(groups[in_dd]):
        span = (segment.index[-1] - segment.index[0]).total_seconds() / 86400
        max_days = max(max_days, span)
    return int(max_days)


def _exposure_pct(closed_trades: list, equity: pd.Series) -> float:
    if equity.empty or not closed_trades:
        return 0.0
    total_span = (equity.index[-1] - equity.index[0]).total_seconds()
    if total_span <= 0:
        return 0.0
    in_market = sum(
        (t.exit_time - t.entry_time).total_seconds()
        for t in closed_trades
        if t.exit_time is not None
    )
    return min(in_market / total_span * 100, 100.0)
