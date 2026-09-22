from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.backtest.engine import TradeRecord


@dataclass
class MonteCarloResult:
    n_sims: int
    method: str
    return_pct_p5: float
    return_pct_p50: float
    return_pct_p95: float
    max_dd_p5: float  # peor escenario (mas negativo)
    max_dd_p50: float
    max_dd_p95: float  # mejor escenario
    prob_loss_pct: float
    prob_ruin_pct: float  # probabilidad de un drawdown mayor a `ruin_threshold_pct`
    ruin_threshold_pct: float
    returns_distribution: list[float]
    drawdowns_distribution: list[float]


def run_monte_carlo(
    trades: list[TradeRecord],
    initial_capital: float,
    n_sims: int = 1000,
    method: Literal["shuffle", "bootstrap"] = "shuffle",
    ruin_threshold_pct: float = 20.0,
    seed: int | None = None,
) -> MonteCarloResult | None:
    """Robustez de la estrategia frente al ORDEN (y, en modo bootstrap, la
    composicion) de sus propias operaciones: si el resultado del backtest
    depende de que las operaciones hayan caido en ese orden particular, la
    estrategia es fragil aunque el backtest original se vea bien.

    - "shuffle": reordena aleatoriamente las mismas operaciones (permutacion,
      sin repetir ninguna) -> mide sensibilidad al orden.
    - "bootstrap": muestrea las operaciones CON reemplazo (algunas se repiten,
      otras no aparecen) -> ademas mide sensibilidad a la composicion exacta
      de la muestra de operaciones observada.
    """
    returns_pct = [t.pnl_pct for t in trades if t.pnl_pct is not None]
    if len(returns_pct) < 10:
        return None

    rng = np.random.default_rng(seed)
    returns_arr = np.array(returns_pct)

    final_returns_pct = np.empty(n_sims)
    max_drawdowns_pct = np.empty(n_sims)

    for i in range(n_sims):
        sample = (
            rng.permutation(returns_arr)
            if method == "shuffle"
            else rng.choice(returns_arr, size=len(returns_arr), replace=True)
        )
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in sample:
            equity *= 1 + r
            peak = max(peak, equity)
            dd = (equity - peak) / peak * 100
            max_dd = min(max_dd, dd)
        final_returns_pct[i] = (equity - 1) * 100
        max_drawdowns_pct[i] = max_dd

    return MonteCarloResult(
        n_sims=n_sims,
        method=method,
        return_pct_p5=round(float(np.percentile(final_returns_pct, 5)), 2),
        return_pct_p50=round(float(np.percentile(final_returns_pct, 50)), 2),
        return_pct_p95=round(float(np.percentile(final_returns_pct, 95)), 2),
        max_dd_p5=round(float(np.percentile(max_drawdowns_pct, 5)), 2),
        max_dd_p50=round(float(np.percentile(max_drawdowns_pct, 50)), 2),
        max_dd_p95=round(float(np.percentile(max_drawdowns_pct, 95)), 2),
        prob_loss_pct=round(float((final_returns_pct < 0).mean() * 100), 2),
        prob_ruin_pct=round(float((max_drawdowns_pct < -ruin_threshold_pct).mean() * 100), 2),
        ruin_threshold_pct=ruin_threshold_pct,
        returns_distribution=final_returns_pct.tolist(),
        drawdowns_distribution=max_drawdowns_pct.tolist(),
    )
