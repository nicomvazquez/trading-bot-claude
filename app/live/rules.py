"""Reglas de decision de la operativa en vivo, como funciones puras (sin base de datos ni exchange)
para poder testearlas. El runner y el servicio de instancias las usan."""

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Literal


# ------------------------------------------------------------------ una posicion por simbolo

def find_symbol_conflict(instances: Iterable, symbol: str, exclude_id: int | None = None):
    """Bybit (modo one-way) tiene UNA sola posicion por simbolo y cuenta: dos instancias operando el mismo
    simbolo terminan fusionando sus ordenes y adoptando la posicion de la otra. Devuelve la instancia
    activa que ya opera `symbol` (distinta de `exclude_id`), o None."""
    symbol = symbol.strip().upper()
    for instance in instances:
        if instance.id != exclude_id and instance.is_active and instance.symbol.strip().upper() == symbol:
            return instance
    return None


def conflict_message(other_name: str, symbol: str) -> str:
    return (
        f"«{other_name}» ya está operando {symbol}. Bybit permite una sola posición por símbolo en la cuenta, así que "
        f"dos instancias sobre el mismo símbolo se pisarían. Apagá esa instancia o usá otro símbolo."
    )


def should_adopt_position(has_local_trade: bool, has_exchange_position: bool, symbol_open_elsewhere: bool) -> bool:
    """Se adopta una posicion del exchange sin registro propio solo si NINGUNA instancia tiene un trade abierto
    en ese simbolo: si otra instancia lo tiene, la posicion es de ella."""
    return has_exchange_position and not has_local_trade and not symbol_open_elsewhere


def closing_side(position_side: str) -> str:
    """Lado de la orden reduce-only que cierra una posicion: vender para cerrar un largo, comprar para un corto."""
    return "Sell" if position_side == "long" else "Buy"


# ------------------------------------------------------------------ velas

CandleAction = Literal["skip_initial", "evaluate", "wait"]


def decide_candle(last_seen: dt.datetime | None, newest_closed: dt.datetime, skip_initial: bool = True) -> CandleAction:
    """Que hacer con la ultima vela cerrada. Al arrancar (last_seen=None) esa vela ya estaba cerrada antes de
    encender la instancia: evaluarla puede abrir una posicion por una senal vieja, de hasta un timeframe atras.
    Se toma como referencia y se espera a la proxima vela cerrada."""
    if last_seen is None:
        return "skip_initial" if skip_initial else "evaluate"
    return "evaluate" if newest_closed > last_seen else "wait"


# ------------------------------------------------------------------ PnL de cierre

@dataclass
class ClosedPnl:
    avg_exit_price: float
    closed_pnl: float
    updated_time: dt.datetime
    records: int


def summarize_closed_pnl(records: Iterable[dict], since: dt.datetime) -> ClosedPnl | None:
    """Suma los cierres registrados por Bybit desde `since` (apertura del trade). Antes se tomaba el ultimo
    registro del simbolo, que podia ser de otro trade. Un cierre en varias ordenes genera varios registros:
    el PnL es la suma y el precio de salida el del ultimo."""
    mine = sorted((r for r in records if r["updated_time"] >= since), key=lambda r: r["updated_time"])
    if not mine:
        return None
    last = mine[-1]
    return ClosedPnl(
        avg_exit_price=last["avg_exit_price"], closed_pnl=sum(r["closed_pnl"] for r in mine),
        updated_time=last["updated_time"], records=len(mine),
    )


# ------------------------------------------------------------------ comisiones y funding reales

@dataclass
class TradeCosts:
    fees: float      # comisiones de entrada + salida, siempre >= 0 (costo)
    funding: float    # neto: negativo = se pago funding, positivo = se cobro (mismo signo que en el backtest)


def summarize_trade_costs(executions: Iterable[dict]) -> TradeCosts:
    """A partir de las ejecuciones reales de Bybit (entradas, salidas y liquidaciones de funding) del
    simbolo mientras el trade estuvo abierto, separa cuanto se pago de comisiones y cuanto de funding.
    Cada ejecucion trae `exec_type` ("Trade" o "Funding", tal como lo informa Bybit) y `fee`, en la
    convencion de Bybit: positivo = cargo a la cuenta, negativo = credito (rebate maker, o funding cobrado)."""
    fees = sum(e["fee"] for e in executions if e["exec_type"] == "Trade")
    funding = sum(-e["fee"] for e in executions if e["exec_type"] == "Funding")
    return TradeCosts(fees=fees, funding=funding)
