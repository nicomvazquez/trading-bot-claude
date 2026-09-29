"""Corre validacion fuera de muestra sobre las 7 estrategias registradas, en BTCUSDT y con sus
parametros por defecto, sobre ~2 anios de historia. Paso 3 del roadmap: antes de confiarles mas
capital real, ver cuales se sostienen fuera de muestra y cuales solo andaban bien en el periodo
con el que se probaron a mano.

Para cada estrategia corre:
- Un backtest completo del periodo entero (headline: retorno, Sharpe, drawdown, etc).
- Walk-forward con PARAMETROS FIJOS (sin re-optimizar) en ventanas rodantes no solapadas: mide si
  la estrategia sigue funcionando en tramos que nunca vio, sin el sesgo de reoptimizar por ventana.
- Monte Carlo (bootstrap) sobre las operaciones del backtest completo: distribucion de resultados
  posibles si la estrategia siguiera generando operaciones del mismo tipo.

No re-optimiza parametros: eso queda para una segunda pasada sobre las que sobrevivan esta.

Uso (necesita DB para el cache de velas/funding y red para la API publica de Bybit):
    docker compose exec app python scripts/validate_strategies.py
"""

import asyncio
import datetime as dt
import json

from app.backtest.config import BacktestConfig, ExecutionConfig
from app.backtest.monte_carlo import run_monte_carlo
from app.backtest.service import prepare_market_data, simulate
from app.backtest.validation import run_walk_forward
from app.strategies import registry

DAYS = 700
INITIAL_CAPITAL = 1000.0
TRAIN_DAYS, TEST_DAYS, STEP_DAYS = 180, 60, 60
MC_SIMS, MC_SEED = 2000, 42

# No hay un timeframe "oficial" por estrategia en el codigo: se elige segun la naturaleza de cada
# una (ver su `description` en el catalogo del dashboard).
TIMEFRAMES = {
    "donchian_breakout": "60",
    "funding_oi": "240",
    "rsi_reversion": "15",
    "sma_cross": "15",
    "trend_pullback": "60",
    "volatility_squeeze": "60",
    "ict_sweep_fvg": "15",
}


async def validate_one(key: str, timeframe: str) -> dict:
    strategy_cls = registry.get(key)
    params = strategy_cls.params_model()
    config = BacktestConfig(
        symbol="BTCUSDT", timeframe=timeframe, days=DAYS, initial_capital=INITIAL_CAPITAL,
        execution=ExecutionConfig(funding_mode="historical"),
    )
    print(f"[{key}] descargando datos ({timeframe}, {DAYS}d)...", flush=True)
    market = await prepare_market_data(config, strategy_cls)
    if market.candles.empty:
        return {"key": key, "timeframe": timeframe, "error": "sin datos"}

    print(f"[{key}] backtest completo del periodo...", flush=True)
    full_result, full_metrics = await asyncio.to_thread(simulate, strategy_cls, params, config, market)
    if "error" in full_metrics:
        return {"key": key, "timeframe": timeframe, "error": full_metrics["error"]}

    print(f"[{key}] walk-forward (train {TRAIN_DAYS}d / test {TEST_DAYS}d / paso {STEP_DAYS}d, parametros fijos)...", flush=True)
    wf = await asyncio.to_thread(
        run_walk_forward, strategy_cls, params, config, market, TRAIN_DAYS, TEST_DAYS, STEP_DAYS, None,
    )

    mc = None
    if (full_metrics.get("num_trades") or 0) >= 10:
        print(f"[{key}] monte carlo ({MC_SIMS} sims, bootstrap)...", flush=True)
        mc = run_monte_carlo(full_result.trades, INITIAL_CAPITAL, n_sims=MC_SIMS, method="bootstrap", seed=MC_SEED)

    return {
        "key": key,
        "timeframe": timeframe,
        "full": {
            "num_trades": full_metrics.get("num_trades"),
            "total_return_pct": full_metrics.get("total_return_pct"),
            "cagr_pct": full_metrics.get("cagr_pct"),
            "max_drawdown_pct": full_metrics.get("max_drawdown_pct"),
            "sharpe_ratio": full_metrics.get("sharpe_ratio"),
            "sortino_ratio": full_metrics.get("sortino_ratio"),
            "profit_factor": full_metrics.get("profit_factor"),
            "win_rate_pct": full_metrics.get("win_rate_pct"),
        },
        "walk_forward": {
            "n_windows": wf.aggregate.get("n_windows"),
            "n_evaluated": wf.aggregate.get("n_evaluated"),
            "pct_profitable": wf.aggregate.get("pct_profitable"),
            "return_mean": wf.aggregate.get("return_mean"),
            "return_median": wf.aggregate.get("return_median"),
            "return_min": wf.aggregate.get("return_min"),
            "return_max": wf.aggregate.get("return_max"),
            "worst_drawdown": wf.aggregate.get("worst_drawdown"),
            "compounded_return_pct": wf.aggregate.get("compounded_return_pct"),
            "warnings": wf.warnings,
        },
        "monte_carlo": None if mc is None else {
            "return_pct_p5": mc.return_pct_p5,
            "return_pct_p50": mc.return_pct_p50,
            "return_pct_p95": mc.return_pct_p95,
            "prob_loss_pct": mc.prob_loss_pct,
            "prob_ruin_pct": mc.prob_ruin_pct,
            "max_dd_p5": mc.max_dd_p5,
            "max_dd_p50": mc.max_dd_p50,
        },
    }


async def main() -> None:
    started = dt.datetime.now()
    results = []
    for key, timeframe in TIMEFRAMES.items():
        try:
            results.append(await validate_one(key, timeframe))
        except Exception as exc:  # noqa: BLE001
            print(f"[{key}] ERROR: {exc}", flush=True)
            results.append({"key": key, "timeframe": timeframe, "error": str(exc)})
    print(f"\nTerminado en {(dt.datetime.now() - started).total_seconds():.0f}s")
    print("\n===RESULTS_JSON===")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
