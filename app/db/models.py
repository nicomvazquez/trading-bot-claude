import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    timeframe: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    # Cuando se descargo la vela. Si se bajo antes de que cerrara, su OHLC puede
    # estar incompleto y hay que volver a pedirla ("sospechosa").
    fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FundingRate(Base):
    __tablename__ = "funding_rates"
    __table_args__ = (UniqueConstraint("symbol", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    rate: Mapped[float] = mapped_column(Float)  # fraccion (0.0001 = 0.01%)


class OpenInterest(Base):
    __tablename__ = "open_interest"
    __table_args__ = (UniqueConstraint("symbol", "interval", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, index=True)
    interval: Mapped[str] = mapped_column(String)  # 5min | 15min | 30min | 1h | 4h | 1d
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[float] = mapped_column(Float)  # contratos abiertos (unidades del activo)


class StrategyInstance(Base):
    __tablename__ = "strategy_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    strategy_key: Mapped[str] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String)
    timeframe: Mapped[str] = mapped_column(String)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    capital_allocation_pct: Mapped[float] = mapped_column(Float, default=0.0)
    initial_capital: Mapped[float] = mapped_column(Float, default=100.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )

    orders: Mapped[list["Order"]] = relationship(back_populates="strategy_instance")
    trades: Mapped[list["Trade"]] = relationship(back_populates="strategy_instance")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_instance_id: Mapped[int] = mapped_column(ForeignKey("strategy_instances.id"))
    exchange_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    symbol: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)
    order_type: Mapped[str] = mapped_column(String)
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )

    strategy_instance: Mapped["StrategyInstance"] = relationship(back_populates="orders")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_instance_id: Mapped[int] = mapped_column(ForeignKey("strategy_instances.id"))
    symbol: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    qty: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    strategy_instance: Mapped["StrategyInstance"] = relationship(back_populates="trades")


class EquityPoint(Base):
    __tablename__ = "equity_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_instances.id"), nullable=True
    )
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    equity: Mapped[float] = mapped_column(Float)


class StrategyEvent(Base):
    """Bitacora de lo que hace cada instancia en vivo: aperturas, cierres, senales
    rechazadas por riesgo, errores. Es lo que explica por que NO se opero."""

    __tablename__ = "strategy_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_instance_id: Mapped[int] = mapped_column(ForeignKey("strategy_instances.id"), index=True)
    timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )
    kind: Mapped[str] = mapped_column(String)  # started | stopped | opened | closed | rejected | error
    message: Mapped[str] = mapped_column(String)


class BotSettings(Base):
    """Fila unica (id=1) con los limites de riesgo globales y el kill-switch."""

    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=5.0)
    max_concurrent_positions: Mapped[int] = mapped_column(Integer, default=5)
    max_leverage: Mapped[float] = mapped_column(Float, default=10.0)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_key: Mapped[str] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String)
    timeframe: Mapped[str] = mapped_column(String)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    start_date: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )
