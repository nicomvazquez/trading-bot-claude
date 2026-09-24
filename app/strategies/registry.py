from app.strategies.base import Strategy

_registry: dict[str, type[Strategy]] = {}


def register(strategy_cls: type[Strategy]) -> type[Strategy]:
    """Decorador: registra una estrategia para que aparezca automaticamente
    en el dashboard (seccion Estrategias) y en el backtester."""
    _registry[strategy_cls.key] = strategy_cls
    return strategy_cls


def get_all() -> dict[str, type[Strategy]]:
    return dict(_registry)


def get(key: str) -> type[Strategy]:
    return _registry[key]


def _load_builtin_strategies() -> None:
    from app.strategies.examples import (  # noqa: F401
        donchian_breakout, funding_oi, rsi_reversion, sma_cross, trend_pullback, volatility_squeeze,
    )
    from app.strategies.ict import sweep_fvg  # noqa: F401


_load_builtin_strategies()
