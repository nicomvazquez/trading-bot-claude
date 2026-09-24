"""Monte Carlo sobre la secuencia de operaciones de un backtest.

Se remuestrean los RETORNOS NETOS por operacion (PnL neto / equity al abrirla)
y se recomponen como equity compuesto. Tres metodos, con objetivos distintos:

- shuffle (permutacion de la secuencia): las MISMAS operaciones en otro orden.
  El retorno final es identico en todas las simulaciones (el producto de los
  factores es conmutativo); lo que cambia es el CAMINO, o sea el drawdown.
  Mide sensibilidad a la secuencia.
- bootstrap (muestreo con reemplazo, i.i.d.): algunas operaciones se repiten y
  otras no aparecen. Varia tambien el retorno final: estima la distribucion de
  resultados posibles si la estrategia siguiera generando operaciones del mismo
  tipo. Supone operaciones independientes entre si.
- block_bootstrap (bloques circulares): igual que bootstrap pero copiando
  bloques consecutivos, lo que conserva la dependencia serial de corto plazo
  (rachas de ganancias o perdidas).

Limitacion: el drawdown se mide sobre el equity a nivel de operacion cerrada,
asi que no captura la caida dentro de una operacion. Con pocas operaciones la
distribucion simulada es poco confiable."""

from dataclasses import dataclass, field

import numpy as np

from app.backtest.engine import TradeRecord

METHODS = ("shuffle", "bootstrap", "block_bootstrap")
MIN_TRADES = 10
RELIABLE_TRADES = 30
_CHUNK = 4000


@dataclass
class MonteCarloResult:
    n_sims: int
    method: str
    n_trades: int
    seed: int  # semilla efectivamente usada (si no se dio una, se genera y se registra)
    block_size: int | None
    return_pct_p5: float
    return_pct_p25: float
    return_pct_p50: float
    return_pct_p75: float
    return_pct_p95: float
    return_pct_mean: float
    max_dd_p5: float  # peor escenario (mas negativo)
    max_dd_p50: float
    max_dd_p95: float  # mejor escenario
    prob_loss_pct: float
    prob_ruin_pct: float  # probabilidad de un drawdown peor que `ruin_threshold_pct`
    ruin_threshold_pct: float
    actual_return_pct: float  # resultado real (en el orden original)
    actual_max_dd_pct: float
    actual_dd_worse_than_pct: float  # % de simulaciones con drawdown MAS SUAVE que el real
    returns_distribution: list[float]
    drawdowns_distribution: list[float]
    warnings: list[str] = field(default_factory=list)


def _paths_stats(sample: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(retorno final %, drawdown maximo %) de cada camino (fila) de retornos."""
    factors = np.maximum(1.0 + sample, 0.0)  # una perdida >= 100% deja el equity en 0 (absorbente)
    equity = np.cumprod(factors, axis=1)
    peak = np.maximum.accumulate(np.concatenate((np.ones((len(sample), 1)), equity), axis=1), axis=1)[:, 1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdown = np.where(peak > 0, equity / peak - 1.0, 0.0)
    return (equity[:, -1] - 1.0) * 100, drawdown.min(axis=1) * 100


def _sample(returns: np.ndarray, size: int, method: str, block_size: int, rng: np.random.Generator) -> np.ndarray:
    n = len(returns)
    if method == "shuffle":
        return rng.permuted(np.tile(returns, (size, 1)), axis=1)
    if method == "bootstrap":
        return rng.choice(returns, size=(size, n), replace=True)
    blocks = int(np.ceil(n / block_size))
    starts = rng.integers(0, n, size=(size, blocks))
    idx = (starts[:, :, None] + np.arange(block_size)[None, None, :]) % n
    return returns[idx.reshape(size, blocks * block_size)[:, :n]]


def run_monte_carlo(
    trades: list[TradeRecord],
    initial_capital: float,
    n_sims: int = 1000,
    method: str = "shuffle",
    ruin_threshold_pct: float = 20.0,
    seed: int | None = None,
    block_size: int = 5,
    min_trades: int = MIN_TRADES,
) -> MonteCarloResult | None:
    """Devuelve None si no hay operaciones suficientes (`min_trades`)."""
    if method not in METHODS:
        raise ValueError(f"Metodo Monte Carlo invalido: {method}")
    if n_sims < 1:
        raise ValueError("Hacen falta al menos 1 simulacion.")
    if method == "block_bootstrap" and block_size < 1:
        raise ValueError("El tamano de bloque debe ser al menos 1.")

    returns = np.array([t.pnl_pct for t in trades if t.pnl_pct is not None], dtype=float)
    if len(returns) < min_trades:
        return None
    n = len(returns)
    block_size = min(block_size, n)

    if seed is None:
        seed = int(np.random.SeedSequence().generate_state(1)[0])
    rng = np.random.default_rng(seed)

    final_returns = np.empty(n_sims)
    max_drawdowns = np.empty(n_sims)
    for lo in range(0, n_sims, _CHUNK):
        hi = min(lo + _CHUNK, n_sims)
        sample = _sample(returns, hi - lo, method, block_size, rng)
        final_returns[lo:hi], max_drawdowns[lo:hi] = _paths_stats(sample)

    actual_return, actual_dd = (float(v[0]) for v in _paths_stats(returns[None, :]))
    p = lambda arr, q: float(np.percentile(arr, q))  # noqa: E731

    warnings = []
    if n < RELIABLE_TRADES:
        warnings.append(
            f"Solo {n} operaciones: con menos de {RELIABLE_TRADES} la distribucion simulada es poco confiable "
            "(no puede inventar informacion que la muestra no tiene)."
        )
    if method == "bootstrap" and n >= 3 and returns.std() > 1e-12 and abs(np.corrcoef(returns[:-1], returns[1:])[0, 1]) > 0.3:
        warnings.append("Las operaciones muestran dependencia serial: considerá block bootstrap, que la conserva.")
    warnings.append("El drawdown se mide sobre operaciones cerradas: no captura la caida dentro de una operacion.")

    return MonteCarloResult(
        n_sims=n_sims, method=method, n_trades=n, seed=seed,
        block_size=block_size if method == "block_bootstrap" else None,
        return_pct_p5=p(final_returns, 5), return_pct_p25=p(final_returns, 25), return_pct_p50=p(final_returns, 50),
        return_pct_p75=p(final_returns, 75), return_pct_p95=p(final_returns, 95),
        return_pct_mean=float(final_returns.mean()),
        max_dd_p5=p(max_drawdowns, 5), max_dd_p50=p(max_drawdowns, 50), max_dd_p95=p(max_drawdowns, 95),
        prob_loss_pct=float((final_returns < 0).mean() * 100),
        prob_ruin_pct=float((max_drawdowns < -ruin_threshold_pct).mean() * 100),
        ruin_threshold_pct=ruin_threshold_pct,
        actual_return_pct=actual_return, actual_max_dd_pct=actual_dd,
        actual_dd_worse_than_pct=float((max_drawdowns > actual_dd + 1e-9).mean() * 100),
        returns_distribution=final_returns.tolist(), drawdowns_distribution=max_drawdowns.tolist(),
        warnings=warnings,
    )
