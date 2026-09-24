DEFAULT_RISK_PCT = 1.0


def position_size(
    equity: float,
    price: float,
    stop_loss: float | None,
    risk_pct: float | None,
    max_leverage: float = 10.0,
    *,
    mode: str = "risk_based",
    notional_pct: float = 100.0,
    max_position_pct: float | None = None,
    cost_per_unit: float = 0.0,
) -> float:
    """Cantidad a operar. La usan el backtest y la ejecucion en vivo, para que
    el sizing sea identico en los dos.

    - "risk_based": arriesga `risk_pct`% del equity hasta el stop. Sin stop no
      hay riesgo definido: por compatibilidad, `risk_pct`% del equity se usa
      como NOCIONAL. `cost_per_unit` (fees + slippage por unidad) se suma a la
      distancia al stop si se quiere que el riesgo incluya costos.
    - "fixed_notional_pct": nocional fijo como % del equity (ignora el stop).

    En ambos casos el nocional queda topado por `max_leverage` x equity (el
    apalancamiento es un TOPE de exposicion, no define el tamano) y, si se
    indica, por `max_position_pct`% del equity."""
    if equity <= 0 or price <= 0:
        return 0.0

    if mode == "fixed_notional_pct":
        qty = equity * notional_pct / 100 / price
    else:
        risk_pct = risk_pct if risk_pct is not None else DEFAULT_RISK_PCT
        risk_amount = equity * risk_pct / 100
        distance = abs(price - stop_loss) if stop_loss else 0.0
        if distance > 0:
            qty = risk_amount / (distance + cost_per_unit)
        else:
            qty = risk_amount / price

    max_notional = equity * max_leverage
    if max_position_pct is not None:
        max_notional = min(max_notional, equity * max_position_pct / 100)
    return max(min(qty, max_notional / price), 0.0)
