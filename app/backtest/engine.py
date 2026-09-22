import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from app.risk.sizing import position_size
from app.strategies.base import Position, Signal, Strategy, StrategyContext


@dataclass
class TradeRecord:
    side: str  # "long" | "short"
    entry_time: dt.datetime
    entry_price: float
    qty: float
    equity_before: float
    exit_time: dt.datetime | None = None
    exit_price: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None  # retorno del trade sobre el equity al momento de abrirlo
    reason: str = ""
    exit_reason: str = ""  # "stop_loss" | "take_profit" | "senal" | "flip" | "fin del backtest"


@dataclass
class BacktestResult:
    strategy_key: str
    symbol: str
    timeframe: str
    params: dict
    initial_capital: float
    equity_curve: pd.Series
    trades: list[TradeRecord] = field(default_factory=list)


class Backtester:
    """Corre una Strategy (la misma clase que se usaria en vivo) sobre velas
    historicas.

    Modelo de ejecucion:
    - La estrategia decide al CIERRE de una vela; la orden se ejecuta en la
      APERTURA de la vela siguiente (no al mismo cierre que ya vio).
    - Stop-loss y take-profit de la Signal se evaluan dentro de cada vela con
      su maximo y minimo. Si en una misma vela se tocan ambos, se asume que
      se toco primero el stop (el caso conservador). Si la vela abre con gap
      mas alla del stop, se sale en la apertura.
    - El tamano se calcula por % de riesgo hasta el stop, con un tope de
      apalancamiento maximo sobre el equity.
    - No se modelan slippage, funding ni spread."""

    def __init__(self, fee_pct: float = 0.055, min_lookback: int = 50, max_leverage: float = 10.0) -> None:
        self.fee_pct = fee_pct
        self.min_lookback = min_lookback
        self.max_leverage = max_leverage

    def run(
        self,
        strategy: Strategy,
        candles: pd.DataFrame,
        initial_capital: float,
    ) -> BacktestResult:
        equity = initial_capital
        position: Position | None = None
        open_trade: TradeRecord | None = None
        pending: Signal | None = None

        trades: list[TradeRecord] = []
        equity_points: list[tuple[dt.datetime, float]] = []

        n = len(candles)
        opens = candles["open"].to_numpy(dtype=float)
        highs = candles["high"].to_numpy(dtype=float)
        lows = candles["low"].to_numpy(dtype=float)
        closes = candles["close"].to_numpy(dtype=float)
        start_i = min(self.min_lookback, n)

        for i in range(start_i, n):
            ts = candles.index[i]
            o, h, l, c = float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])

            if pending is not None:
                equity, position, open_trade = self._execute(
                    pending, equity, position, open_trade, o, ts, trades
                )
                pending = None

            if position is not None:
                exit_hit = self._check_exit(position, o, h, l)
                if exit_hit is not None:
                    exit_price, exit_reason = exit_hit
                    equity, position, open_trade = self._close(
                        equity, position, open_trade, exit_price, ts, exit_reason
                    )
                    if open_trade is not None:
                        trades.append(open_trade)
                        open_trade = None

            if i < n - 1:
                ctx = StrategyContext(candles=candles.iloc[: i + 1], position=position, equity=equity)
                pending = strategy.on_candle(ctx)

            unrealized = 0.0
            if position is not None:
                direction = 1 if position.side == "long" else -1
                unrealized = direction * (c - position.entry_price) * position.qty
            equity_points.append((ts, equity + unrealized))

        if position is not None and n > 0:
            last_price = float(closes[-1])
            last_ts = candles.index[-1]
            equity, _, open_trade = self._close(
                equity, position, open_trade, last_price, last_ts, "fin del backtest"
            )
            if open_trade is not None:
                trades.append(open_trade)
            if equity_points:
                equity_points[-1] = (last_ts, equity)

        equity_curve = pd.Series(dict(equity_points)) if equity_points else pd.Series(dtype=float)
        for t in trades:
            if t.pnl is not None:
                t.pnl_pct = t.pnl / t.equity_before if t.equity_before else 0.0

        return BacktestResult(
            strategy_key=strategy.key,
            symbol="",
            timeframe="",
            params=strategy.params.model_dump(),
            initial_capital=initial_capital,
            equity_curve=equity_curve,
            trades=trades,
        )

    def _execute(
        self,
        signal: Signal,
        equity: float,
        position: Position | None,
        open_trade: TradeRecord | None,
        price: float,
        ts: dt.datetime,
        trades: list[TradeRecord],
    ) -> tuple[float, Position | None, TradeRecord | None]:
        if signal.action == "close":
            if position is not None:
                equity, position, open_trade = self._close(equity, position, open_trade, price, ts, "senal")
                if open_trade is not None:
                    trades.append(open_trade)
                    open_trade = None
            return equity, position, open_trade

        desired_side = "long" if signal.action == "buy" else "short"
        if position is not None and position.side == desired_side:
            return equity, position, open_trade

        if position is not None:
            equity, position, open_trade = self._close(equity, position, open_trade, price, ts, "flip")
            if open_trade is not None:
                trades.append(open_trade)
                open_trade = None

        stop_loss = signal.stop_loss
        take_profit = signal.take_profit
        if stop_loss is not None and (
            (desired_side == "long" and stop_loss >= price) or (desired_side == "short" and stop_loss <= price)
        ):
            return equity, None, None  # el precio de apertura ya paso el stop: no se entra
        if take_profit is not None and (
            (desired_side == "long" and take_profit <= price) or (desired_side == "short" and take_profit >= price)
        ):
            take_profit = None

        qty = position_size(equity, price, stop_loss, signal.risk_pct, self.max_leverage)
        if qty <= 0:
            return equity, None, None

        fee = qty * price * self.fee_pct / 100
        equity_before = equity
        equity -= fee
        position = Position(
            side=desired_side, entry_price=price, qty=qty, stop_loss=stop_loss, take_profit=take_profit
        )
        open_trade = TradeRecord(
            side=desired_side,
            entry_time=ts,
            entry_price=price,
            qty=qty,
            equity_before=equity_before,
            reason=signal.reason,
        )
        return equity, position, open_trade

    @staticmethod
    def _check_exit(position: Position, o: float, h: float, l: float) -> tuple[float, str] | None:
        sl, tp = position.stop_loss, position.take_profit
        if position.side == "long":
            if sl is not None and l <= sl:
                return min(o, sl), "stop_loss"
            if tp is not None and h >= tp:
                return max(o, tp), "take_profit"
        else:
            if sl is not None and h >= sl:
                return max(o, sl), "stop_loss"
            if tp is not None and l <= tp:
                return min(o, tp), "take_profit"
        return None

    def _close(
        self,
        equity: float,
        position: Position,
        open_trade: TradeRecord | None,
        price: float,
        ts: dt.datetime,
        reason: str,
    ) -> tuple[float, None, TradeRecord | None]:
        direction = 1 if position.side == "long" else -1
        pnl = direction * (price - position.entry_price) * position.qty
        fee = position.qty * price * self.fee_pct / 100
        equity = equity + pnl - fee
        if open_trade is not None:
            open_trade.exit_time = ts
            open_trade.exit_price = price
            open_trade.pnl = pnl - fee
            open_trade.exit_reason = reason
        return equity, None, open_trade
