import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.backtest.config import ConfigError, ExecutionConfig, RiskConfig
from app.backtest.quality import bar_delta
from app.risk.sizing import position_size
from app.strategies.base import Position, Signal, Strategy, StrategyContext


class DataError(ValueError):
    """Las velas no sirven para simular (vacias, con NaN, precios invalidos...)."""


@dataclass
class TradeRecord:
    side: str  # "long" | "short"
    entry_time: dt.datetime
    entry_price: float
    qty: float
    equity_before: float
    exit_time: dt.datetime | None = None
    exit_price: float | None = None
    pnl: float | None = None  # NETO: bruto - comisiones de entrada y salida + funding
    pnl_pct: float | None = None  # retorno neto sobre el equity al abrir la operacion
    reason: str = ""
    exit_reason: str = ""  # stop_loss | take_profit | senal | flip | fin del backtest
    entry_fee: float = 0.0
    id: int = 0
    exit_fee: float = 0.0
    funding: float = 0.0  # con signo: negativo = se pago funding
    gross_pnl: float | None = None
    slippage_cost: float = 0.0  # costo de slippage/spread ya incluido en los precios (informativo)
    open_at_end: bool = False  # cerrada a la fuerza al terminar los datos
    stop_loss: float | None = None
    take_profit: float | None = None
    capped_by_leverage: bool = False

    @property
    def fees(self) -> float:
        return self.entry_fee + self.exit_fee

    @property
    def duration(self) -> dt.timedelta | None:
        if self.exit_time is None:
            return None
        return self.exit_time - self.entry_time

    @property
    def notional(self) -> float:
        return self.qty * self.entry_price


@dataclass
class BacktestResult:
    strategy_key: str
    symbol: str
    timeframe: str
    params: dict
    initial_capital: float
    equity_curve: pd.Series
    trades: list[TradeRecord] = field(default_factory=list)
    exposure: pd.Series | None = None  # +1 largo / -1 corto / 0 sin posicion, por vela
    diagnostics: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    bar_seconds: float = 0.0


@dataclass
class _State:
    cash: float
    position: Position | None = None
    trade: TradeRecord | None = None
    pending: Signal | None = None
    pending_ref: float = 0.0  # cierre de la vela que genero la senal
    pending_age: int = 0
    trades: list[TradeRecord] = field(default_factory=list)
    next_id: int = 1


class Backtester:
    """Corre una Strategy (la misma clase que se usaria en vivo) sobre velas
    historicas.

    Modelo de ejecucion (todo configurable con ExecutionConfig / RiskConfig):
    - La estrategia decide al CIERRE de una vela y ve solo datos hasta ese
      cierre. La orden se ejecuta en la APERTURA de la vela siguiente
      ("next_open"); "same_close" ejecuta en el cierre de la misma vela y es
      optimista.
    - Ordenes "market": taker + slippage + mitad del spread, siempre en contra.
      Ordenes "limit": maker, sin slippage; solo se ejecutan si el precio las
      alcanza (una orden que no se ejecuta se pierde: es un costo real).
    - Stop-loss: orden stop-market (taker + slippage). Take-profit: orden
      limit (maker, sin slippage). Si en una vela se tocan ambos niveles se
      asume stop primero, salvo que haya un timeframe menor para resolverlo.
      Si la vela abre con gap mas alla del nivel, se ejecuta en la apertura.
    - Funding: se cobra en cada instante de funding con posicion abierta.
    - Sizing: por riesgo hasta el stop (o nocional fijo); el apalancamiento es
      solo un tope de nocional/equity, no define el tamano.
    - Una posicion por vez (sin pyramiding). No se modelan liquidacion ni
      margen. El drawdown se mide al cierre de cada vela, no intravela."""

    def __init__(
        self,
        fee_pct: float = 0.055,
        min_lookback: int = 50,
        max_leverage: float = 10.0,
        *,
        execution: ExecutionConfig | None = None,
        risk: RiskConfig | None = None,
        funding_rates: pd.Series | None = None,
        intrabar_candles: pd.DataFrame | None = None,
    ) -> None:
        self.execution = execution or ExecutionConfig(taker_fee_pct=fee_pct)
        self.risk = risk or RiskConfig(max_leverage=max_leverage)
        self.min_lookback = min_lookback
        self.funding_rates = funding_rates
        self._fund_idx = self._fund_vals = None
        if funding_rates is not None and not funding_rates.empty:
            self._fund_idx = pd.DatetimeIndex(funding_rates.index)
            self._fund_vals = funding_rates.to_numpy(dtype=float)
        self._sub_idx = self._sub_h = self._sub_l = None
        if intrabar_candles is not None and not intrabar_candles.empty:
            self._sub_idx = pd.DatetimeIndex(intrabar_candles.index)
            self._sub_h = intrabar_candles["high"].to_numpy(dtype=float)
            self._sub_l = intrabar_candles["low"].to_numpy(dtype=float)

    # ------------------------------------------------------------------ run

    def run(
        self,
        strategy: Strategy,
        candles: pd.DataFrame,
        initial_capital: float,
        trade_start: dt.datetime | None = None,
    ) -> BacktestResult:
        """`trade_start`: las velas anteriores solo sirven de contexto (calentar
        indicadores) y no se opera en ellas; permite medir un tramo sin perder
        historia previa, y sin usar datos del futuro."""
        errors = self.execution.validate() + self.risk.validate()
        if not initial_capital or initial_capital <= 0:
            errors.append("El capital inicial debe ser positivo.")
        if errors:
            raise ConfigError(" ".join(errors))
        self._validate_candles(candles)

        n = len(candles)
        idx = candles.index
        bar = self._infer_bar(idx)
        opens = candles["open"].to_numpy(dtype=float)
        highs = candles["high"].to_numpy(dtype=float)
        lows = candles["low"].to_numpy(dtype=float)
        closes = candles["close"].to_numpy(dtype=float)

        start_i = min(self.min_lookback, n)
        if trade_start is not None:
            start_i = min(max(start_i, int(idx.searchsorted(pd.Timestamp(trade_start)))), n)

        st = _State(cash=float(initial_capital))
        diag = {
            "skipped_signals": {}, "capped_by_leverage": 0, "limit_orders_expired": 0,
            "intrabar_resolved": 0, "intrabar_unavailable": 0, "funding_events": 0,
            "open_at_end": False, "bars_simulated": max(n - start_i, 0),
        }
        equity_t: list = []
        equity_v: list[float] = []
        exposure_v: list[int] = []
        same_close = self.execution.execution_model == "same_close"

        for i in range(start_i, n):
            ts = idx[i]
            o, h, l, c = float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])
            events = self._funding_events(ts, bar)

            # 1) funding de la posicion que llega de velas anteriores
            if st.position is not None and events:
                self._apply_funding(st, events, o, diag)

            # 2) orden pendiente de la vela anterior
            entered_this_bar = False
            entered_intrabar = False
            if st.pending is not None:
                entered_this_bar, entered_intrabar = self._process_pending(st, o, h, l, ts, diag)

            exposure_v.append(0 if st.position is None else (1 if st.position.side == "long" else -1))

            # 3) stop-loss / take-profit dentro de la vela
            if st.position is not None:
                hit = self._check_exit(st.position, o, h, l, ts, bar, diag, skip_tp=entered_intrabar)
                if hit is not None:
                    ref, reason, kind = hit
                    self._close_position(st, ref, ts, reason, kind)

            # funding para una posicion abierta durante esta vela (no en su apertura)
            if entered_this_bar and st.position is not None and events:
                self._apply_funding(st, events, o, diag, only_after=ts)

            # 4) decision de la estrategia con datos hasta el cierre de esta vela
            if i < n - 1:
                signal = strategy.on_candle(
                    StrategyContext(candles=candles.iloc[: i + 1], position=st.position, equity=st.cash)
                )
                if signal is not None:
                    st.pending, st.pending_ref, st.pending_age = signal, c, 0
                    if same_close:
                        self._process_pending(st, c, c, c, ts, diag)

            unrealized = 0.0
            if st.position is not None:
                sign = 1 if st.position.side == "long" else -1
                unrealized = sign * (c - st.position.entry_price) * st.position.qty
            equity_t.append(ts)
            equity_v.append(st.cash + unrealized)

        if st.position is not None and n > 0:
            self._close_position(st, float(closes[-1]), idx[-1], "fin del backtest", "market")
            st.trades[-1].open_at_end = True
            diag["open_at_end"] = True
            if equity_v:
                equity_v[-1] = st.cash

        for t in st.trades:
            if t.pnl is not None and t.equity_before:
                t.pnl_pct = t.pnl / t.equity_before

        return BacktestResult(
            strategy_key=strategy.key,
            symbol="",
            timeframe="",
            params=strategy.params.model_dump(),
            initial_capital=float(initial_capital),
            equity_curve=pd.Series(equity_v, index=pd.DatetimeIndex(equity_t), dtype=float),
            trades=st.trades,
            exposure=pd.Series(exposure_v, index=pd.DatetimeIndex(equity_t), dtype=int),
            diagnostics=diag,
            bar_seconds=bar.total_seconds(),
        )

    # ------------------------------------------------------------ validation

    @staticmethod
    def _validate_candles(candles: pd.DataFrame) -> None:
        if candles is None or candles.empty:
            raise DataError("No hay velas para simular.")
        missing = {"open", "high", "low", "close"} - set(candles.columns)
        if missing:
            raise DataError(f"Faltan columnas de precio: {sorted(missing)}")
        ohlc = candles[["open", "high", "low", "close"]]
        if ohlc.isna().any().any():
            raise DataError("Las velas tienen precios NaN: hay que limpiarlas antes de simular.")
        if (ohlc <= 0).any().any():
            raise DataError("Las velas tienen precios no positivos.")
        if not candles.index.is_monotonic_increasing or candles.index.has_duplicates:
            raise DataError("Las velas deben estar ordenadas por tiempo y sin duplicados.")

    @staticmethod
    def _infer_bar(idx: pd.DatetimeIndex) -> pd.Timedelta:
        if len(idx) < 2:
            return bar_delta("60")
        return pd.Series(idx[1:] - idx[:-1]).median()

    # -------------------------------------------------------------- funding

    def _funding_events(self, ts: pd.Timestamp, bar: pd.Timedelta) -> list[tuple[pd.Timestamp, float]]:
        mode = self.execution.funding_mode
        if mode == "none":
            return []
        end = ts + bar
        if mode == "constant":
            rate = self.execution.funding_rate_pct / 100
            events = []
            f = ts.ceil("8h")
            while f < end:
                events.append((f, rate))
                f += pd.Timedelta(hours=8)
            return events
        if self._fund_idx is None:
            return []
        lo, hi = self._fund_idx.searchsorted(ts), self._fund_idx.searchsorted(end)
        return [(self._fund_idx[k], float(self._fund_vals[k])) for k in range(lo, hi)]

    def _apply_funding(self, st: _State, events, price: float, diag: dict, only_after=None) -> None:
        sign = 1 if st.position.side == "long" else -1
        for when, rate in events:
            if only_after is not None and when <= only_after:
                continue
            if self.execution.funding_adverse:
                payment = -st.position.qty * price * abs(rate)  # estres: siempre es un costo, sea largo o corto
            else:
                payment = -sign * st.position.qty * price * rate  # rate > 0: los largos pagan
            st.cash += payment
            st.trade.funding += payment
            diag["funding_events"] += 1

    # ------------------------------------------------------------ execution

    def _adverse(self, price: float, buying: bool) -> float:
        bps = self.execution.market_adverse_bps / 10_000
        return price * (1 + bps) if buying else price * (1 - bps)

    def _skip(self, diag: dict, reason: str) -> None:
        diag["skipped_signals"][reason] = diag["skipped_signals"].get(reason, 0) + 1

    def _process_pending(self, st: _State, o: float, h: float, l: float, ts, diag: dict) -> tuple[bool, bool]:
        """Ejecuta la orden pendiente. Devuelve (abrio posicion en esta vela,
        la abrio en un punto intravela distinto de la apertura)."""
        signal = st.pending

        if signal.action == "close":
            if st.position is not None:
                self._close_position(st, o, ts, "senal", "market")
            st.pending = None
            return False, False

        desired = "long" if signal.action == "buy" else "short"
        if st.position is not None and st.position.side == desired:
            st.pending = None
            return False, False
        if st.position is not None:
            self._close_position(st, o, ts, "flip", "market")

        buying = desired == "long"
        if self.execution.order_type == "limit":
            limit = signal.limit_price if signal.limit_price is not None else st.pending_ref
            fillable = l <= limit if buying else h >= limit
            if not fillable:
                st.pending_age += 1
                if st.pending_age >= self.execution.limit_ttl_bars:
                    st.pending = None
                    diag["limit_orders_expired"] += 1
                return False, False
            ref = min(o, limit) if buying else max(o, limit)
            opened = self._open_position(st, signal, desired, ref, ts, diag, maker=True)
            st.pending = None
            return opened, opened and ref != o

        opened = self._open_position(st, signal, desired, o, ts, diag, maker=False)
        st.pending = None
        return opened, False

    def _open_position(self, st: _State, signal: Signal, side: str, ref: float, ts, diag: dict, maker: bool) -> bool:
        buying = side == "long"
        fill = ref if maker else self._adverse(ref, buying)

        stop_loss, take_profit = signal.stop_loss, signal.take_profit
        if stop_loss is not None and ((buying and stop_loss >= fill) or (not buying and stop_loss <= fill)):
            self._skip(diag, "stop_ya_superado_al_abrir")
            return False
        if take_profit is not None and ((buying and take_profit <= fill) or (not buying and take_profit >= fill)):
            take_profit = None

        fee_pct = self.execution.effective_maker_fee_pct if maker else self.execution.taker_fee_pct
        cost_per_unit = 0.0
        if self.risk.risk_includes_costs:
            cost_per_unit = 2 * fill * (self.execution.taker_fee_pct + self.execution.market_adverse_bps / 100) / 100

        risk_pct = self.risk.risk_per_trade_pct if self.risk.risk_per_trade_pct is not None else signal.risk_pct
        common = dict(
            mode=self.risk.sizing_mode, notional_pct=self.risk.notional_pct_of_equity, cost_per_unit=cost_per_unit,
        )
        qty = position_size(
            st.cash, fill, stop_loss, risk_pct, self.risk.max_leverage,
            max_position_pct=self.risk.max_position_pct_of_equity, **common,
        )
        if qty <= 0:
            self._skip(diag, "tamano_cero")
            return False
        unconstrained = position_size(st.cash, fill, stop_loss, risk_pct, 1e9, max_position_pct=None, **common)
        capped = qty < unconstrained * (1 - 1e-9)
        if capped:
            diag["capped_by_leverage"] += 1

        fee = qty * fill * fee_pct / 100
        st.trade = TradeRecord(
            side=side, entry_time=ts, entry_price=fill, qty=qty, equity_before=st.cash,
            reason=signal.reason, entry_fee=fee, id=st.next_id, stop_loss=stop_loss,
            take_profit=take_profit, capped_by_leverage=capped, slippage_cost=qty * abs(fill - ref),
        )
        st.next_id += 1
        st.cash -= fee
        st.position = Position(side=side, entry_price=fill, qty=qty, stop_loss=stop_loss, take_profit=take_profit)
        return True

    def _check_exit(self, position: Position, o: float, h: float, l: float, ts, bar, diag: dict, skip_tp: bool = False):
        """(precio de referencia, motivo, tipo de orden) o None. `skip_tp`: la
        posicion se abrio a mitad de la vela, y el maximo pudo ocurrir antes."""
        sl, tp = position.stop_loss, position.take_profit
        long = position.side == "long"
        sl_hit = sl is not None and ((l <= sl) if long else (h >= sl))
        tp_hit = (not skip_tp) and tp is not None and ((h >= tp) if long else (l <= tp))
        if not sl_hit and not tp_hit:
            return None
        if sl_hit and tp_hit and self.execution.intrabar_resolution:
            if self._resolve_intrabar(position, ts, bar, diag) == "tp":
                sl_hit = False
        if sl_hit:
            return (min(o, sl) if long else max(o, sl)), "stop_loss", "stop"
        return (max(o, tp) if long else min(o, tp)), "take_profit", "limit"

    def _resolve_intrabar(self, position: Position, ts, bar, diag: dict) -> str | None:
        """Que nivel se toco primero, mirando las velas del timeframe menor."""
        if self._sub_idx is None:
            diag["intrabar_unavailable"] += 1
            return None
        lo, hi = self._sub_idx.searchsorted(ts), self._sub_idx.searchsorted(ts + bar)
        if hi <= lo:
            diag["intrabar_unavailable"] += 1
            return None
        sl, tp = position.stop_loss, position.take_profit
        long = position.side == "long"
        for k in range(lo, hi):
            sl_hit = (self._sub_l[k] <= sl) if long else (self._sub_h[k] >= sl)
            tp_hit = (self._sub_h[k] >= tp) if long else (self._sub_l[k] <= tp)
            if sl_hit and tp_hit:
                diag["intrabar_unavailable"] += 1
                return None  # ni la sub-vela lo resuelve: stop primero
            if sl_hit or tp_hit:
                diag["intrabar_resolved"] += 1
                return "stop" if sl_hit else "tp"
        return None

    def _close_position(self, st: _State, ref: float, ts, reason: str, kind: str) -> None:
        """kind: 'market' y 'stop' pagan taker + slippage; 'limit' (take-profit) paga maker."""
        position, trade = st.position, st.trade
        selling = position.side == "long"  # cerrar un largo = vender
        if kind == "limit":
            fill, fee_pct = ref, self.execution.effective_maker_fee_pct
        else:
            fill, fee_pct = self._adverse(ref, buying=not selling), self.execution.taker_fee_pct
            if kind == "stop" and self.execution.stop_slippage_bps:
                extra = self.execution.stop_slippage_bps / 10_000  # estres: el stop se ejecuta peor que lo normal
                fill = fill * (1 - extra) if selling else fill * (1 + extra)

        sign = 1 if position.side == "long" else -1
        gross = sign * (fill - position.entry_price) * position.qty
        exit_fee = position.qty * fill * fee_pct / 100
        st.cash += gross - exit_fee

        trade.exit_time, trade.exit_price = ts, fill
        trade.gross_pnl, trade.exit_fee = gross, exit_fee
        trade.slippage_cost += position.qty * abs(fill - ref)
        # PnL neto: incluye ambas comisiones (la de entrada ya se desconto del
        # efectivo al abrir) y el funding (ya aplicado al efectivo): asi la
        # suma de PnL de los trades cuadra con la variacion del equity.
        trade.pnl = gross - trade.entry_fee - exit_fee + trade.funding
        trade.exit_reason = reason
        st.trades.append(trade)
        st.position = st.trade = None
