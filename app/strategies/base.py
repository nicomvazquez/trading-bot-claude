from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Literal

import pandas as pd
from pydantic import BaseModel


@dataclass
class Position:
    side: Literal["long", "short"]
    entry_price: float
    qty: float
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass
class StrategyContext:
    """Lo que recibe la estrategia en cada vela nueva."""

    candles: pd.DataFrame  # columnas: open, high, low, close, volume ; index: timestamp
    position: Position | None
    equity: float


@dataclass
class Signal:
    action: Literal["buy", "sell", "close"]
    reason: str = ""
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_pct: float | None = None  # % del equity a arriesgar; si es None, usa el default del RiskManager
    limit_price: float | None = None  # solo para ordenes limit; si es None se usa el cierre de la vela de la senal


class Strategy(ABC):
    """Interfaz que toda estrategia debe implementar. La misma clase se usa
    tanto en vivo como en backtest, para que el comportamiento sea identico
    en los dos casos."""

    key: ClassVar[str]
    display_name: ClassVar[str]
    params_model: ClassVar[type[BaseModel]]
    # datos ademas de las velas que la estrategia lee como columnas de ctx.candles:
    # "funding" -> funding_rate ; "open_interest" -> open_interest
    required_data: ClassVar[tuple[str, ...]] = ()
    description: ClassVar[str] = ""  # una o dos frases: que hace la estrategia y cuando opera
    version: ClassVar[str] = "1"  # subir cuando cambia la logica: queda registrado en cada backtest

    def __init__(self, params: BaseModel) -> None:
        self.params = params

    @abstractmethod
    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        """Se llama con cada vela cerrada nueva. Devuelve una Signal o None
        si no hay que hacer nada."""
        raise NotImplementedError
