DEFAULT_RISK_PCT = 1.0


def position_size(
    equity: float,
    price: float,
    stop_loss: float | None,
    risk_pct: float | None,
    max_leverage: float = 10.0,
) -> float:
    """Cantidad a operar por % de equity arriesgado hasta el stop-loss, con
    un tope de apalancamiento sobre el equity. La usan tanto el backtest
    como la ejecucion en vivo, para que el sizing sea identico en los dos."""
    risk_pct = risk_pct if risk_pct is not None else DEFAULT_RISK_PCT
    risk_amount = equity * risk_pct / 100
    if stop_loss and abs(price - stop_loss) > 0:
        qty = risk_amount / abs(price - stop_loss)
    else:
        qty = risk_amount / price
    max_qty = equity * max_leverage / price
    return min(qty, max_qty)
