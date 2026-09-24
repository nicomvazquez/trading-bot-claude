"""Generador de datos de demostracion (instancias, operaciones, ordenes, bitacora y backtests) para ver
los paneles con contenido. Es puro (no toca la base ni el exchange) y determinista dada la semilla.

Los datos son sinteticos: los precios siguen una curva suave alrededor de un valor base y el resultado de
cada operacion se sortea segun la tasa de aciertos de cada estrategia. NO representan resultados reales."""

import datetime as dt
import math
import random
import uuid
from dataclasses import dataclass, field

FEE_RATE = 0.00055  # comision taker de Bybit por lado

# simbolo -> (precio base, paso de cantidad, decimales de precio)
SYMBOLS = {"BTCUSDT": (84000.0, 0.001, 1), "ETHUSDT": (2700.0, 0.01, 2), "SOLUSDT": (135.0, 0.1, 3)}


class NotADemoDatabase(RuntimeError):
    pass


def assert_demo_database(db_name: str) -> None:
    """El sembrado borra todas las tablas: solo se permite sobre una base cuyo nombre termine en `_demo`."""
    if not db_name.endswith("_demo"):
        raise NotADemoDatabase(
            f"Se niega a sembrar la base «{db_name}»: el sembrado borra todo y solo corre sobre bases que terminan en «_demo»."
        )


@dataclass
class Spec:
    name: str
    key: str
    symbol: str
    timeframe: str
    capital: float
    win_rate: float
    win_r: float               # ganancia promedio de las operaciones ganadoras, en multiplos del riesgo
    stop_pct: float
    sides: str                 # "long" | "both"
    trades: int
    hold_hours: tuple[float, float]
    take_profit_rr: float | None = None
    open_now: bool = False
    notes: list[str] = field(default_factory=list)  # eventos extra de la bitacora


SPECS = [
    Spec("rsi_reversion-btc", "rsi_reversion", "BTCUSDT", "15", 1000, 0.60, 0.9, 3.0, "long", 30, (1, 10),
         notes=["rejected:Señal de long descartada: el tamaño calculado (0.000280) es menor al mínimo de BTCUSDT (0.001). Subí el capital o el % de riesgo de la instancia."]),
    Spec("sma_cross-eth", "sma_cross", "ETHUSDT", "60", 2000, 0.38, 2.1, 2.0, "both", 26, (6, 60), open_now=True),
    Spec("donchian-sol", "donchian_breakout", "SOLUSDT", "60", 1500, 0.36, 2.4, 2.5, "both", 28, (8, 70)),
    Spec("squeeze-btc-4h", "volatility_squeeze", "BTCUSDT", "240", 3000, 0.32, 3.0, 2.2, "both", 14, (24, 110), open_now=True),
    Spec("pullback-eth-15m", "trend_pullback", "ETHUSDT", "15", 1000, 0.34, 1.5, 0.9, "both", 36, (0.5, 6), take_profit_rr=2.0,
         notes=["error:Error en el ciclo: HTTPSConnectionPool(host='api-demo.bybit.com', port=443): Read timed out."]),
    Spec("funding-oi-btc", "funding_oi", "BTCUSDT", "60", 2000, 0.47, 1.7, 2.5, "both", 12, (10, 90), take_profit_rr=2.0),
]

RUNS = [
    ("donchian_breakout", "BTCUSDT", "60", {"total_return_pct": 22.65, "sharpe_ratio": 0.98, "max_drawdown_pct": -17.31, "num_trades": 162}),
    ("donchian_breakout", "BTCUSDT", "240", {"total_return_pct": 11.51, "sharpe_ratio": 1.03, "max_drawdown_pct": -9.53, "num_trades": 44}),
    ("volatility_squeeze", "BTCUSDT", "240", {"total_return_pct": 18.70, "sharpe_ratio": 1.12, "max_drawdown_pct": -15.10, "num_trades": 59}),
    ("volatility_squeeze", "BTCUSDT", "60", {"total_return_pct": -22.36, "sharpe_ratio": -1.01, "max_drawdown_pct": -32.67, "num_trades": 188}),
    ("trend_pullback", "BTCUSDT", "60", {"total_return_pct": -22.25, "sharpe_ratio": -1.61, "max_drawdown_pct": -27.20, "num_trades": 122}),
    ("funding_oi", "BTCUSDT", "60", {"total_return_pct": -3.29, "sharpe_ratio": -1.51, "max_drawdown_pct": -4.31, "num_trades": 24}),
]


def price_at(symbol: str, moment: dt.datetime, rng: random.Random) -> float:
    base, _, decimals = SYMBOLS[symbol]
    days = moment.timestamp() / 86400
    phase = sum(map(ord, symbol)) % 7
    drift = 0.06 * math.sin(days / 9 + phase) + 0.025 * math.sin(days / 2.3 + phase)
    return round(base * (1 + drift + rng.gauss(0, 0.002)), decimals)


def generate(now: dt.datetime | None = None, seed: int = 7, specs: list[Spec] | None = None) -> dict:
    """Devuelve {instances, trades, orders, events, runs}. Cada trade/orden/evento referencia la instancia por nombre."""
    now = now or dt.datetime.now(dt.timezone.utc)
    rng = random.Random(seed)
    out: dict = {"instances": [], "trades": [], "orders": [], "events": [], "runs": []}
    window_start = now - dt.timedelta(days=45)

    for spec in specs or SPECS:
        _, step, decimals = SYMBOLS[spec.symbol]
        from app.strategies import registry  # import tardio: evita ciclos al importar el paquete

        params = registry.get(spec.key).params_model().model_dump()
        out["instances"].append({
            "name": spec.name, "strategy_key": spec.key, "symbol": spec.symbol, "timeframe": spec.timeframe,
            "params": params, "initial_capital": spec.capital, "is_active": False,
            "created_at": window_start - dt.timedelta(hours=2),
        })
        events = [(window_start, "started", f"Instancia encendida ({spec.symbol} {spec.timeframe}m)")]
        t = window_start + dt.timedelta(hours=rng.uniform(1, 30))
        closed = []
        while len(closed) < spec.trades:
            hold = dt.timedelta(hours=rng.uniform(*spec.hold_hours))
            if t + hold > now - dt.timedelta(minutes=30):
                break
            side = "long" if spec.sides == "long" else rng.choice(["long", "short"])
            direction = 1 if side == "long" else -1
            entry = price_at(spec.symbol, t, rng)
            stop_dist = entry * spec.stop_pct / 100
            if rng.random() < spec.win_rate:
                r = max(0.3, rng.gauss(spec.win_r, spec.win_r * 0.35))
                reason = "take_profit" if rng.random() < 0.6 else "senal"
            else:
                r = -1.0 if rng.random() < 0.75 else -rng.uniform(0.3, 0.9)
                reason = "stop_loss" if r == -1.0 else "senal"
            if reason in ("take_profit", "stop_loss") and rng.random() < 0.5:
                reason = "stop_loss_o_take_profit (exchange)"
            exit_price = round(entry + direction * r * stop_dist, decimals)
            qty = max(math.floor(spec.capital * 0.01 / stop_dist / step) * step, step)
            qty = round(qty, 6)
            fees = (entry + exit_price) * qty * FEE_RATE
            pnl = round(direction * (exit_price - entry) * qty - fees, 4)
            trade = {
                "instance": spec.name, "symbol": spec.symbol, "side": side, "entry_price": entry, "exit_price": exit_price,
                "qty": qty, "pnl": pnl, "stop_loss": round(entry - direction * stop_dist, decimals),
                "take_profit": round(entry + direction * stop_dist * spec.take_profit_rr, decimals) if spec.take_profit_rr else None,
                "exit_reason": reason, "opened_at": t, "closed_at": t + hold,
            }
            out["trades"].append(trade)
            closed.append(trade)
            _orders(out, spec, trade, rng, with_exit=True)
            t = t + hold + dt.timedelta(hours=rng.uniform(0.5, 16))

        for trade in closed[-6:]:
            events.append((trade["opened_at"], "opened", f"Abierta {trade['side']} {trade['qty']:g} {spec.symbol} @ {trade['entry_price']:,.2f} · stop {trade['stop_loss']:,.2f}"))
            events.append((trade["closed_at"], "closed", f"Cerrada por {trade['exit_reason']}, PnL {trade['pnl']:+.4f} USD"))

        if spec.open_now:
            opened = now - dt.timedelta(hours=rng.uniform(1, 5))
            side = "long" if spec.sides == "long" else rng.choice(["long", "short"])
            direction = 1 if side == "long" else -1
            entry = price_at(spec.symbol, opened, rng)
            stop_dist = entry * spec.stop_pct / 100
            qty = round(max(math.floor(spec.capital * 0.01 / stop_dist / step) * step, step), 6)
            trade = {
                "instance": spec.name, "symbol": spec.symbol, "side": side, "entry_price": entry, "exit_price": None, "qty": qty,
                "pnl": None, "stop_loss": round(entry - direction * stop_dist, decimals),
                "take_profit": round(entry + direction * stop_dist * 2, decimals) if spec.take_profit_rr else None,
                "exit_reason": None, "opened_at": opened, "closed_at": None,
            }
            out["trades"].append(trade)
            _orders(out, spec, trade, rng, with_exit=False)
            events.append((opened, "opened", f"Abierta {side} {qty:g} {spec.symbol} @ {entry:,.2f} · stop {trade['stop_loss']:,.2f}"))

        for note in spec.notes:
            kind, message = note.split(":", 1)
            events.append((now - dt.timedelta(hours=rng.uniform(2, 30)), kind, message))
        for moment, kind, message in events:
            out["events"].append({"instance": spec.name, "timestamp": moment, "kind": kind, "message": message})

    for i, (key, symbol, timeframe, metrics) in enumerate(RUNS):
        from app.strategies import registry

        out["runs"].append({
            "strategy_key": key, "symbol": symbol, "timeframe": timeframe, "params": registry.get(key).params_model().model_dump(),
            "start_date": now - dt.timedelta(days=365), "end_date": now, "metrics": metrics,
            "created_at": now - dt.timedelta(hours=3 + 9 * i),
        })
    return out


def _orders(out: dict, spec: Spec, trade: dict, rng: random.Random, with_exit: bool) -> None:
    entry_side = "Buy" if trade["side"] == "long" else "Sell"
    base = {"instance": spec.name, "symbol": spec.symbol, "order_type": "Market", "qty": trade["qty"], "status": "filled"}
    out["orders"].append({**base, "side": entry_side, "price": trade["entry_price"], "created_at": trade["opened_at"],
                          "exchange_order_id": str(uuid.UUID(int=rng.getrandbits(128)))})
    if with_exit:
        out["orders"].append({**base, "side": "Sell" if entry_side == "Buy" else "Buy", "price": trade["exit_price"],
                              "created_at": trade["closed_at"], "exchange_order_id": str(uuid.UUID(int=rng.getrandbits(128)))})
