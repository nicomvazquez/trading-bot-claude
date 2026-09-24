import numpy as np
from pydantic import BaseModel, Field

from app.strategies.base import Signal, Strategy, StrategyContext
from app.strategies.registry import register


class FundingOiParams(BaseModel):
    lookback: int = Field(default=180, ge=30, le=1000, description="Velas para medir qué tan extremo es el funding (percentil)")
    funding_high_pct: float = Field(default=90, ge=60, le=99.5, description="Percentil de funding desde el cual los largos están apretados")
    funding_low_pct: float = Field(default=10, ge=0.5, le=40, description="Percentil de funding hasta el cual los cortos están apretados")
    oi_bars: int = Field(default=12, ge=1, le=200, description="Velas sobre las que se mide el cambio del open interest")
    oi_min_change_pct: float = Field(default=1.0, ge=0.0, le=50.0, description="Suba mínima del open interest (%) para considerar que el posicionamiento se acumula")
    trigger_bars: int = Field(default=6, ge=2, le=100, description="Ruptura: cierre fuera del rango de las últimas N velas")
    allow_long: bool = Field(default=True, description="Permitir largos (cuando los cortos están apretados)")
    allow_short: bool = Field(default=True, description="Permitir cortos (cuando los largos están apretados)")
    exit_pct: float = Field(default=50, ge=20, le=80, description="Cierra cuando el percentil del funding vuelve a este nivel (posicionamiento normalizado)")
    stop_loss_pct: float = Field(default=2.5, ge=0.2, le=20.0, description="% de stop-loss respecto al precio de entrada")
    rr_ratio: float = Field(default=2.0, ge=0.5, le=10.0, description="Take profit como múltiplo del riesgo (R)")
    risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar por operación")


@register
class FundingOiStrategy(Strategy):
    """Posicionamiento contrarian: opera contra el lado que está demasiado apretado.

    1. Funding extremo: su percentil dentro de las últimas `lookback` velas supera `funding_high_pct`
       (largos apretados) o cae bajo `funding_low_pct` (cortos apretados).
    2. Acumulación: el open interest subió al menos `oi_min_change_pct` en `oi_bars` velas, es decir,
       entró posicionamiento nuevo (no es solo un cierre de posiciones).
    3. Disparador: el precio rompe en contra del lado apretado (cierra bajo el mínimo / sobre el máximo
       de las últimas `trigger_bars` velas): recién ahí se entra. Sin disparador, un funding extremo
       puede durar semanas.
    4. Salida: stop fijo, take profit en múltiplo del riesgo, o cuando el funding se normaliza."""

    key = "funding_oi"
    display_name = "Posicionamiento: Funding + Open Interest"
    description = (
        "Opera contra el lado apretado del mercado: cuando el funding está en un extremo y el open interest sigue "
        "subiendo (mucha gente cargada en la misma dirección), espera que el precio rompa en contra y entra en esa "
        "dirección. Sale por stop, take profit o cuando el funding se normaliza. Señales lentas (horas o días): "
        "conviene timeframes de 1 a 4 horas."
    )
    params_model = FundingOiParams
    required_data = ("funding", "open_interest")

    def on_candle(self, ctx: StrategyContext) -> Signal | None:
        p: FundingOiParams = self.params
        candles = ctx.candles
        need = max(p.lookback, p.oi_bars + 1, p.trigger_bars + 1)
        if len(candles) < need or "funding_rate" not in candles or "open_interest" not in candles:
            return None

        window = candles.iloc[-need:]
        funding = window["funding_rate"].to_numpy(dtype=float)[-p.lookback:]
        oi = window["open_interest"].to_numpy(dtype=float)
        if np.isnan(funding[-1]) or np.isnan(oi[-1]) or np.isnan(oi[-1 - p.oi_bars]) or oi[-1 - p.oi_bars] <= 0:
            return None
        valid = funding[~np.isnan(funding)]
        if len(valid) < p.lookback // 2:
            return None

        pct_rank = float((valid <= funding[-1]).mean() * 100)  # percentil del funding actual
        oi_change = (oi[-1] / oi[-1 - p.oi_bars] - 1) * 100
        price = float(window["close"].iloc[-1])

        if ctx.position is not None:
            # posicionamiento normalizado: la razon de la operacion desaparecio
            if ctx.position.side == "short" and pct_rank <= p.exit_pct:
                return Signal(action="close", reason=f"funding normalizado (percentil {pct_rank:.0f})")
            if ctx.position.side == "long" and pct_rank >= p.exit_pct:
                return Signal(action="close", reason=f"funding normalizado (percentil {pct_rank:.0f})")
            return None

        if oi_change < p.oi_min_change_pct:
            return None

        lows = window["low"].to_numpy(dtype=float)[-(p.trigger_bars + 1):-1]
        highs = window["high"].to_numpy(dtype=float)[-(p.trigger_bars + 1):-1]
        stop_dist = price * p.stop_loss_pct / 100

        if p.allow_short and pct_rank >= p.funding_high_pct and price < lows.min():
            return Signal(
                action="sell",
                reason=f"largos apretados (funding p{pct_rank:.0f}, OI {oi_change:+.1f}%) y ruptura a la baja",
                stop_loss=price + stop_dist, take_profit=price - stop_dist * p.rr_ratio, risk_pct=p.risk_pct,
            )
        if p.allow_long and pct_rank <= p.funding_low_pct and price > highs.max():
            return Signal(
                action="buy",
                reason=f"cortos apretados (funding p{pct_rank:.0f}, OI {oi_change:+.1f}%) y ruptura al alza",
                stop_loss=price - stop_dist, take_profit=price + stop_dist * p.rr_ratio, risk_pct=p.risk_pct,
            )
        return None
