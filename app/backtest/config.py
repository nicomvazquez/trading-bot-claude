"""Configuracion del backtest, separada por responsabilidad:

- ExecutionConfig: como se ejecutan las ordenes (tipo, costos, funding).
- RiskConfig: cuanto se arriesga y cuanto apalancamiento se permite. El
  APALANCAMIENTO es solo un tope de exposicion (nocional / equity); el TAMANO
  de cada posicion sale del sizing (riesgo por operacion hasta el stop).
- ValidationConfig: parametros de Monte Carlo y de validacion fuera de muestra.

Los valores por defecto reproducen el comportamiento historico del motor."""

import datetime as dt
from dataclasses import asdict, dataclass, field, fields


class ConfigError(ValueError):
    """Configuracion invalida (se informa al usuario, no se ejecuta el backtest)."""


EXECUTION_MODELS = ("next_open", "same_close")
ORDER_TYPES = ("market", "limit")
FUNDING_MODES = ("none", "constant", "historical")
SIZING_MODES = ("risk_based", "fixed_notional_pct")
MC_METHODS = ("shuffle", "bootstrap", "block_bootstrap")


@dataclass
class ExecutionConfig:
    # "next_open": la orden se ejecuta en la apertura de la vela siguiente a la senal.
    # "same_close": en el cierre de la MISMA vela que genero la senal (optimista).
    execution_model: str = "next_open"
    # "market": taker, con slippage/spread. "limit": maker, solo si el precio la alcanza.
    order_type: str = "market"
    limit_ttl_bars: int = 1  # velas que una orden limit sigue viva
    taker_fee_pct: float = 0.055
    maker_fee_pct: float | None = None  # None = igual al taker (conservador)
    slippage_bps: float = 0.0
    spread_bps: float = 0.0  # spread completo; en ordenes market se paga la mitad
    funding_mode: str = "none"
    funding_rate_pct: float = 0.01  # por periodo de 8h, modo "constant" (positivo: longs pagan)
    intrabar_resolution: bool = False  # resolver SL/TP ambiguos con un timeframe menor

    @property
    def effective_maker_fee_pct(self) -> float:
        return self.taker_fee_pct if self.maker_fee_pct is None else self.maker_fee_pct

    @property
    def market_adverse_bps(self) -> float:
        return self.slippage_bps + self.spread_bps / 2

    def validate(self) -> list[str]:
        errors = []
        if self.execution_model not in EXECUTION_MODELS:
            errors.append(f"Modelo de ejecucion invalido: {self.execution_model}")
        if self.order_type not in ORDER_TYPES:
            errors.append(f"Tipo de orden invalido: {self.order_type}")
        if self.funding_mode not in FUNDING_MODES:
            errors.append(f"Modo de funding invalido: {self.funding_mode}")
        if self.taker_fee_pct < 0 or (self.maker_fee_pct is not None and self.maker_fee_pct < -0.05):
            errors.append("Las comisiones no pueden ser negativas (un rebate maker no puede superar 0.05%).")
        if self.taker_fee_pct > 5:
            errors.append("La comision taker es inverosimil (>5%).")
        if self.slippage_bps < 0 or self.spread_bps < 0:
            errors.append("Slippage y spread no pueden ser negativos.")
        if self.limit_ttl_bars < 1:
            errors.append("La vigencia de una orden limit debe ser al menos 1 vela.")
        return errors


@dataclass
class RiskConfig:
    # "risk_based": tamano = (equity * riesgo%) / distancia al stop.
    # "fixed_notional_pct": nocional = % fijo del equity (ignora el stop).
    sizing_mode: str = "risk_based"
    risk_per_trade_pct: float | None = None  # si se define, anula el riesgo de la senal
    notional_pct_of_equity: float = 100.0  # solo para fixed_notional_pct
    max_leverage: float = 10.0  # tope de nocional / equity
    max_position_pct_of_equity: float | None = None  # tope adicional de nocional (% del equity)
    risk_includes_costs: bool = False  # sumar fees/slippage a la distancia al stop en el sizing

    def validate(self) -> list[str]:
        errors = []
        if self.sizing_mode not in SIZING_MODES:
            errors.append(f"Modo de sizing invalido: {self.sizing_mode}")
        if not (1 <= self.max_leverage <= 125):
            errors.append("El apalancamiento debe estar entre 1x y 125x.")
        if self.risk_per_trade_pct is not None and not (0 < self.risk_per_trade_pct <= 100):
            errors.append("El riesgo por operacion debe estar entre 0 y 100%.")
        if self.notional_pct_of_equity <= 0:
            errors.append("El nocional como % del equity debe ser positivo.")
        if self.max_position_pct_of_equity is not None and self.max_position_pct_of_equity <= 0:
            errors.append("El tamano maximo de posicion debe ser positivo.")
        return errors


@dataclass
class ValidationConfig:
    mc_method: str = "shuffle"
    mc_sims: int = 1000
    mc_seed: int | None = None
    mc_block_size: int = 5
    mc_ruin_threshold_pct: float = 20.0

    def validate(self) -> list[str]:
        errors = []
        if self.mc_method not in MC_METHODS:
            errors.append(f"Metodo Monte Carlo invalido: {self.mc_method}")
        if self.mc_sims < 10:
            errors.append("Monte Carlo necesita al menos 10 simulaciones.")
        if self.mc_block_size < 1:
            errors.append("El tamano de bloque debe ser al menos 1.")
        return errors


@dataclass
class BacktestConfig:
    symbol: str = "BTCUSDT"
    timeframe: str = "60"
    days: int = 90
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    initial_capital: float = 1000.0
    exchange: str = "bybit"
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)

    def validate(self) -> list[str]:
        errors = self.execution.validate() + self.risk.validate() + self.validation.validate()
        if self.initial_capital is None or self.initial_capital <= 0:
            errors.append("El capital inicial debe ser positivo.")
        if not self.symbol:
            errors.append("Falta el simbolo.")
        if self.days < 1:
            errors.append("El periodo debe ser de al menos 1 dia.")
        return errors

    def validate_or_raise(self) -> None:
        errors = self.validate()
        if errors:
            raise ConfigError(" ".join(errors))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["start"] = self.start.isoformat() if self.start else None
        data["end"] = self.end.isoformat() if self.end else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "BacktestConfig":
        def build(klass, values):
            allowed = {f.name for f in fields(klass)}
            return klass(**{k: v for k, v in (values or {}).items() if k in allowed})

        top = {k: v for k, v in data.items() if k not in ("execution", "risk", "validation", "start", "end")}
        allowed = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in top.items() if k in allowed})
        cfg.execution = build(ExecutionConfig, data.get("execution"))
        cfg.risk = build(RiskConfig, data.get("risk"))
        cfg.validation = build(ValidationConfig, data.get("validation"))
        cfg.start = dt.datetime.fromisoformat(data["start"]) if data.get("start") else None
        cfg.end = dt.datetime.fromisoformat(data["end"]) if data.get("end") else None
        return cfg
