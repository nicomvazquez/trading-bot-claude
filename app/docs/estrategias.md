# Guía de estrategias

Una **estrategia** es un conjunto de reglas que, mirando las velas de un mercado, decide cuándo **comprar** (abrir un largo), **vender** (abrir un corto) o **cerrar**. El sistema trae siete. Todas se usan igual: se crea una *instancia* en **Estrategias**, o se prueba primero en **Backtesting**. La misma lógica corre en el backtest y en vivo, así que lo que ves en uno es lo que hace en el otro.

> **Aviso.** Ninguna de estas estrategias tiene una ventaja demostrada. Son puntos de partida para investigar. Un resultado bueno en un backtest no garantiza nada a futuro, y varias de ellas pierden dinero con sus parámetros por defecto (ver sección 10).

## 1. Cómo elegir

| Estrategia | Tipo | Dirección | Timeframe pensado | Gana cuando… | Pierde cuando… |
|---|---|---|---|---|---|
| Reversión a la media (RSI) | Contrarian | Solo largos | 15 min – 1 h | El precio cae y rebota (lateral) | Hay una tendencia bajista sostenida |
| Cruce de medias (SMA) | Tendencia | Solo largos | 1 h – 4 h | Hay tendencias alcistas largas | El mercado es lateral (muchos falsos cruces) |
| ICT: barrida + FVG | Estructura de precio | Largos y cortos | 15 min (sesgo en 4 h) | Hay barridas de liquidez limpias en horas activas | Mercado sin estructura o de baja liquidez |
| Funding + Open Interest | Posicionamiento | Largos y cortos | 1 h – 4 h | Un lado del mercado está sobrecargado y se corrige | El posicionamiento extremo persiste semanas |
| Ruptura de canal (Donchian) | Tendencia | Largos y cortos | 1 h – 4 h | Hay tendencias fuertes con volatilidad creciente | Mercado lateral (muchas pérdidas chicas) |
| Compresión y expansión | Ruptura de volatilidad | Largos y cortos | 1 h – 4 h | Tras un período tranquilo llega un movimiento | Las rupturas fallan y vuelven |
| Retroceso a favor de la tendencia | Tendencia + entrada en retroceso | Largos y cortos | 15 min – 1 h | Tendencias limpias con retrocesos ordenados | Tendencia que se agota o cambia |

**Ideas para combinarlas:** las de tendencia (SMA, Donchian, Retroceso) y las contrarian (RSI, Funding + OI) tienden a ganar en mercados distintos, por eso tiene sentido probarlas juntas, cada una sobre **un símbolo distinto** (una sola instancia activa por símbolo).

## 2. Reglas comunes a todas

- Deciden **solo con velas cerradas**: nunca con la vela en formación.
- La entrada se ejecuta **al abrir la vela siguiente** a la señal (en backtest y en vivo, de forma equivalente).
- **Una posición a la vez** por instancia.
- Todas dimensionan por **riesgo fijo**: arriesgan `risk_pct` % del capital de la instancia hasta el stop (ver Manual de uso, 4.3).
- Los parámetros de riesgo y de stop (`risk_pct`, `stop_loss_pct`, `stop_atr_mult`) cambian mucho el resultado. No los toques a ciegas: probalos en el backtester.

---

## 3. Reversión a la media (RSI) — `rsi_reversion`

**Idea.** Después de una caída fuerte y rápida, el precio suele rebotar. El **RSI** mide esa fuerza de 0 a 100: un valor bajo significa *sobreventa*.

**Reglas.**
1. Si no hay posición y el RSI está **por debajo de `oversold`** → **comprar**, con stop-loss a `stop_loss_pct` % por debajo del precio.
2. Si hay un largo abierto y el RSI **supera `overbought`** → **cerrar**.
3. No abre cortos ni usa take-profit.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `rsi_period` | 14 | 2–100 | Velas del RSI. Menor = más sensible. |
| `oversold` | 30 | 1–49 | Nivel de sobreventa que dispara la compra. Menor = compra menos y en caídas más fuertes. |
| `overbought` | 70 | 51–99 | Nivel que dispara el cierre. |
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado por operación. |
| `stop_loss_pct` | 3.0 | 0.1–20 | Distancia del stop, en % del precio. |

**Detalles que importan.**
- Usa un RSI de **media simple** (no el suavizado de Wilder que muestran algunos gráficos), así que los valores pueden diferir un poco de los de TradingView.
- La condición es de **estado**, no de cruce: mientras el RSI siga bajo `oversold` y no haya posición, puede volver a comprar apenas cierra una operación.

**Cuándo falla.** En una tendencia bajista, compra la caída una y otra vez ("agarrar un cuchillo cayendo") y el stop se activa repetidamente.

**Qué mirar al probarla.** El drawdown y la cantidad de operaciones seguidas perdedoras. Probá `oversold` más bajo (20–25) y compará con la sensibilidad a parámetros.

---

## 4. Cruce de medias móviles (SMA) — `sma_cross`

**Idea.** Cuando una media rápida cruza por encima de una lenta, el precio está tomando impulso alcista.

**Reglas.**
1. La **SMA rápida** (`fast_period`) cruza **hacia arriba** a la **SMA lenta** (`slow_period`) y no hay posición → **comprar**, con stop a `stop_loss_pct` % por debajo.
2. Con un largo abierto, cuando la rápida cruza **hacia abajo** a la lenta → **cerrar**.
3. Solo largos; sin take-profit.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `fast_period` | 10 | 2–200 | Velas de la media rápida. |
| `slow_period` | 50 | 5–400 | Velas de la media lenta. Debe ser mayor que la rápida. |
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado. |
| `stop_loss_pct` | 2.0 | 0.1–20 | Distancia del stop, en %. |

**Cuándo falla.** En mercados laterales, donde las medias se cruzan constantemente: muchas entradas y salidas con pérdidas chicas y comisiones. Es más lenta que otras: entra cuando el movimiento ya empezó.

**Qué mirar.** Pocas operaciones con ganancias grandes es lo esperable (win rate bajo, profit factor alto). Con pocas operaciones, desconfiá de las métricas.

---

## 5. ICT: barrida de liquidez + FVG — `ict_sweep_fvg`

**Idea.** Los grandes participantes suelen empujar el precio para "barrer" los stops que se acumulan tras un máximo o mínimo reciente (la *liquidez*) y luego revertir. Ese movimiento fuerte deja un hueco de precio llamado **Fair Value Gap (FVG)**, al que el precio suele volver antes de continuar.

**Reglas (largo; el corto es simétrico).**
1. **Sesgo (4 h):** solo se opera a favor de la estructura del timeframe superior: máximos y mínimos crecientes → solo largos; decrecientes → solo cortos. Se puede desactivar con `use_htf_bias`.
2. **Liquidez:** un mínimo reciente (swing low) es **barrido**: la mecha lo perfora pero la vela **cierra de nuevo por encima** (rechazo).
3. **Desplazamiento:** una vela alcista fuerte deja un **FVG alcista** (el mínimo de la vela 3 queda por encima del máximo de la vela 1).
4. **Entrada:** en el **primer retroceso al FVG**: la vela toca la zona y cierra por encima de su base. **Stop** debajo de la mecha de la barrida (más `sl_buffer_pct`), **take-profit** a `rr_ratio` veces el riesgo.
5. **Horario:** solo dentro de las **kill zones** (ventanas de mayor actividad), en hora de Nueva York.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado. |
| `rr_ratio` | 2.0 | 0.5–10 | Take-profit como múltiplo del riesgo. |
| `swing_n` | 3 | 2–10 | Velas a cada lado para confirmar un máximo/mínimo local. |
| `liquidity_lookback` | 48 | 10–300 | Cuántas velas atrás se busca la liquidez a barrer. |
| `max_bars_after_sweep` | 12 | 3–60 | Máximo de velas entre la barrida y la entrada. |
| `displacement_atr_mult` | 1.0 | 0–5 | Cuerpo mínimo de la vela de desplazamiento (× ATR de 14; 0 = sin filtro). |
| `min_fvg_pct` | 0.03 | 0–2 | Tamaño mínimo del FVG, en % del precio. |
| `sl_buffer_pct` | 0.05 | 0–2 | Margen del stop más allá de la mecha, en %. |
| `use_htf_bias` | Sí | — | Filtrar por sesgo de estructura en 4 h. |
| `htf_swing_n` | 2 | 1–5 | Velas de 4 h a cada lado para confirmar un swing del sesgo. |
| `use_killzones` | Sí | — | Operar solo en las kill zones. |
| `london_start` / `london_end` | 2 / 5 | 0–24 | Kill zone de Londres (hora de Nueva York). |
| `ny_start` / `ny_end` | 7 / 10 | 0–24 | Kill zone de Nueva York (hora de Nueva York). |

**Detalles.** Pensada para timeframe de **15 minutos**. Cada barrida se usa una sola vez. Al ser un patrón poco frecuente, genera **pocas operaciones**: en períodos cortos, las métricas son poco confiables. El stop es dinámico (depende de dónde quedó la mecha), por eso el *Chequeo de tamaño* al crear la instancia no puede estimar de antemano cuánto abrirá.

**Cuándo falla.** Sin estructura clara, en horarios de baja liquidez o cuando el patrón se detecta pero no hay continuación.

---

## 6. Posicionamiento: Funding + Open Interest — `funding_oi`

**Idea.** Mide **qué tan apretado está un lado del mercado**. Si casi todos están largos, pagan un funding muy alto y, si el open interest sigue subiendo, hay mucha gente cargada en la misma dirección: cualquier caída puede forzar liquidaciones en cadena. La estrategia opera **en contra** del lado apretado, pero solo cuando el precio ya empezó a romper.

**Reglas.**
1. **Funding extremo:** se calcula el *percentil* del funding actual dentro de las últimas `lookback` velas. Percentil ≥ `funding_high_pct` → **largos apretados**. Percentil ≤ `funding_low_pct` → **cortos apretados**.
2. **Acumulación:** el open interest subió al menos `oi_min_change_pct` % en las últimas `oi_bars` velas (entra posicionamiento nuevo, no solo cierres).
3. **Disparador:** el cierre rompe el rango de las últimas `trigger_bars` velas **en contra** del lado apretado. Largos apretados + ruptura a la baja → **vender**. Cortos apretados + ruptura al alza → **comprar**. Sin disparador no entra: un funding extremo puede durar semanas.
4. **Salida:** stop a `stop_loss_pct` %, take-profit a `rr_ratio` veces el riesgo, o cuando el percentil del funding vuelve a `exit_pct` (posicionamiento normalizado).

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `lookback` | 180 | 30–1000 | Velas para el percentil del funding. |
| `funding_high_pct` | 90 | 60–99.5 | Percentil desde el cual los largos están apretados. |
| `funding_low_pct` | 10 | 0.5–40 | Percentil hasta el cual los cortos están apretados. |
| `oi_bars` | 12 | 1–200 | Velas para medir el cambio del open interest. |
| `oi_min_change_pct` | 1.0 | 0–50 | Suba mínima del open interest (%). |
| `trigger_bars` | 6 | 2–100 | Velas del rango que debe romperse. |
| `allow_long` / `allow_short` | Sí | — | Habilitar cada dirección. |
| `exit_pct` | 50 | 20–80 | Percentil del funding al que se cierra. |
| `stop_loss_pct` | 2.5 | 0.2–20 | Distancia del stop, en %. |
| `rr_ratio` | 2.0 | 0.5–10 | Take-profit como múltiplo del riesgo. |
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado. |

**Detalles de datos.** Además de las velas, usa el **funding** y el **open interest** de Bybit, que se descargan solos. Para evitar mirar el futuro, a cada vela se le asigna el último dato que ya se conocía **antes de que abriera**. Es conservador: a veces el dato tiene una vela de antigüedad, pero nunca está adelantado. Conviene timeframe de 1 a 4 horas: el funding cambia cada 8 horas.

**Cuándo falla.** En mercados con tendencia fuerte, donde el posicionamiento extremo persiste y operar en contra sale caro. Genera **muy pocas señales**: la muestra suele ser chica.

---

## 7. Ruptura de canal (Donchian) — `donchian_breakout`

**Idea.** El seguidor de tendencia clásico (estilo *Turtle*): si el precio supera el máximo de las últimas N velas, probablemente empezó una tendencia. Con un filtro: solo si la volatilidad está en expansión.

**Reglas.**
1. **Entrada larga:** el cierre supera el máximo de las últimas `entry_bars` velas **y** el ATR actual es al menos `min_vol_ratio` veces su promedio (`vol_lookback` velas). **Entrada corta:** el cierre rompe el mínimo, con la misma condición.
2. **Stop:** a `stop_atr_mult` × ATR de la entrada.
3. **Salida:** el cierre rompe el canal **corto** de `exit_bars` velas en contra (larga: cierra bajo el mínimo de 10 velas). **No hay take-profit**: deja correr las ganancias.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `entry_bars` | 20 | 5–300 | Ancho del canal de entrada. Mayor = menos señales, más selectivas. |
| `exit_bars` | 10 | 2–200 | Ancho del canal de salida (menor que el de entrada). |
| `atr_period` | 14 | 2–100 | Período del ATR. |
| `vol_lookback` | 50 | 10–500 | Velas para el ATR promedio de referencia. |
| `min_vol_ratio` | 1.0 | 0–5 | ATR actual ÷ promedio mínimo (0 = sin filtro). |
| `stop_atr_mult` | 2.0 | 0.5–10 | Distancia del stop en múltiplos de ATR. |
| `allow_long` / `allow_short` | Sí | — | Habilitar cada dirección. |
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado. |

**Perfil típico.** *Win rate bajo* (alrededor de 35%) y *profit factor* moderado: muchas pérdidas chicas y pocas ganancias grandes. Es psicológicamente incómoda pero es lo esperable de un seguidor de tendencia. Un win rate bajo **no** es un defecto.

**Cuándo falla.** Mercados laterales: cada ruptura falsa cuesta un stop.

---

## 8. Compresión y expansión de volatilidad — `volatility_squeeze`

**Idea.** Los mercados alternan períodos tranquilos y explosivos. Cuando las **bandas de Bollinger** se estrechan mucho (compresión), suele venir un movimiento fuerte. La estrategia espera esa ruptura.

**Reglas.**
1. **Compresión:** el ancho de las bandas cae por debajo del percentil `squeeze_pct` de las últimas `lookback` velas, en alguna de las últimas `squeeze_recent_bars` velas.
2. **Expansión:** el cierre sale de la banda superior → **comprar**; de la inferior → **vender**.
3. **Stop:** a `stop_atr_mult` × ATR. **Salida:** el cierre vuelve a cruzar la **media central** de las bandas. Sin take-profit.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `bb_period` | 20 | 5–200 | Período de las bandas. |
| `bb_std` | 2.0 | 0.5–4 | Desvíos estándar de las bandas. |
| `lookback` | 120 | 30–1000 | Velas para medir qué tan angostas están las bandas. |
| `squeeze_pct` | 20 | 1–50 | Percentil de ancho por debajo del cual hay compresión. Menor = compresiones más extremas y menos señales. |
| `squeeze_recent_bars` | 6 | 1–50 | La compresión debe haber ocurrido en las últimas N velas. |
| `atr_period` | 14 | 2–100 | Período del ATR. |
| `stop_atr_mult` | 2.0 | 0.5–10 | Distancia del stop en ATR. |
| `allow_long` / `allow_short` | Sí | — | Habilitar cada dirección. |
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado. |

**Cuándo falla.** Las rupturas que fallan y vuelven al rango (*falsas rupturas*). En las pruebas fue sensible al timeframe: distinto resultado en 1 hora y en 4 horas, señal de una estrategia frágil.

---

## 9. Retroceso a favor de la tendencia (multi-timeframe) — `trend_pullback`

**Idea.** Es más seguro comprar un retroceso dentro de una tendencia alcista que perseguir el precio. La tendencia se define en un timeframe **superior** y la entrada se busca en el **inferior**.

**Reglas.**
1. **Tendencia (por defecto en 4 horas):** el cierre de la vela de 4 h está por encima (alcista) o por debajo (bajista) de su **EMA** de `htf_ema_period` períodos. Se usan **solo velas de 4 h ya cerradas**: la que está en formación nunca cuenta.
2. **Entrada (timeframe de la instancia):** en tendencia alcista, el RSI venía por debajo de `rsi_pullback` y **vuelve a cruzar ese nivel hacia arriba** (retroceso terminado) → **comprar**. En tendencia bajista, el simétrico con `100 − rsi_pullback` → **vender**.
3. **Stop** a `stop_atr_mult` × ATR y **take-profit** a `rr_ratio` veces el riesgo.
4. **Salida extra:** si la tendencia del timeframe superior se da vuelta (`exit_on_trend_flip`).

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `htf_minutes` | 240 | 30–1440 | Timeframe superior de la tendencia, en minutos. Debe ser mayor que el de la instancia y múltiplo exacto de él. |
| `htf_ema_period` | 50 | 10–200 | Período de la EMA de tendencia. |
| `rsi_period` | 14 | 2–50 | Período del RSI de entrada (RSI de Wilder). |
| `rsi_pullback` | 40 | 10–49 | Nivel de RSI del retroceso. |
| `atr_period` | 14 | 2–100 | Período del ATR. |
| `stop_atr_mult` | 1.5 | 0.5–10 | Distancia del stop en ATR. |
| `rr_ratio` | 2.0 | 0.5–10 | Take-profit como múltiplo del riesgo. |
| `exit_on_trend_flip` | Sí | — | Cerrar si la tendencia superior cambia. |
| `allow_long` / `allow_short` | Sí | — | Habilitar cada dirección. |
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado. |

**Cuidado con los costos.** En timeframes chicos (15 minutos), el stop en ATR queda muy cerca del precio, así que la posición sale grande respecto del capital y **las comisiones consumen buena parte del riesgo de cada operación**. Por eso conviene probarla en 1 hora o más, y siempre con comisión y slippage realistas.

**Cuándo falla.** Cuando la tendencia se agota o cambia: entra en un retroceso que resulta ser el inicio del giro.

---

## 10. Resultados de referencia de nuestras pruebas

Corridas con **parámetros por defecto**, sobre **BTCUSDT**, con comisión taker de 0,055% y **sin** slippage, spread ni funding (por eso son optimistas). Son **en muestra** (no hay validación fuera de muestra) y no deben tomarse como evidencia de ventaja.

| Estrategia | Timeframe | Período | Operaciones | Retorno | Drawdown máx. | Profit factor | Sharpe |
|---|---|---|---|---|---|---|---|
| Donchian | 1 h | 365 días | 162 | +22,7% | −17,3% | 1,24 | 0,98 |
| Donchian | 4 h | 365 días | 44 | +11,5% | −9,5% | 1,48 | 1,03 |
| Compresión | 1 h | 365 días | 188 | −22,4% | −32,7% | 0,79 | −1,01 |
| Compresión | 4 h | 365 días | 59 | +18,7% | −15,1% | 1,54 | 1,12 |
| Retroceso | 15 min | 180 días | 337 | −64,3% | −64,9% | 0,66 | −5,29 |
| Retroceso | 1 h | 365 días | 122 | −22,3% | −27,2% | 0,72 | −1,61 |
| Funding + OI | 1 h | 180 días | 24 | −3,3% | −4,3% | 0,55 | −1,51 |

Cómo leer esa tabla: solo Donchian dio positivo en las dos escalas, y aun así falta validarla. El resultado de Compresión cambia de signo entre 1 y 4 horas. El Retroceso pierde en todas. Funding + OI tuvo apenas 24 operaciones: no alcanza para concluir. Para RSI, SMA e ICT no hay una corrida de referencia registrada en esta guía; probalas vos en el backtester.

**Lo importante es el método:** probar, validar fuera de muestra y con walk-forward, y descartar lo que no aguanta. Ver la **Guía del backtester**.

---

## 11. Cómo crear tu propia estrategia (para desarrolladores)

El sistema descubre las estrategias automáticamente: una nueva aparece sola en el catálogo, en el backtester y en el formulario de parámetros.

1. Creá un archivo en `app/strategies/examples/`, por ejemplo `mi_estrategia.py`.
2. Definí los **parámetros** con un modelo Pydantic (cada `Field` con `description`, límites `ge`/`le` y valor por defecto: la interfaz arma el formulario sola).
3. Definí la clase decorada con `@register`:

   ```python
   from pydantic import BaseModel, Field
   from app.strategies.base import Signal, Strategy, StrategyContext
   from app.strategies.registry import register

   class MisParams(BaseModel):
       periodo: int = Field(default=20, ge=2, le=200, description="Período de la media")
       risk_pct: float = Field(default=1.0, ge=0.1, le=10.0, description="% de equity a arriesgar")

   @register
   class MiEstrategia(Strategy):
       key = "mi_estrategia"
       display_name = "Mi estrategia"
       description = "Una o dos frases: qué hace y cuándo opera."
       params_model = MisParams

       def on_candle(self, ctx: StrategyContext) -> Signal | None:
           # ctx.candles: DataFrame (open, high, low, close, volume) con velas CERRADAS
           # ctx.position: la posición abierta (side, entry_price, qty, ...) o None
           if ctx.position is None and <condición de entrada>:
               price = float(ctx.candles["close"].iloc[-1])
               return Signal(action="buy", reason="por qué", stop_loss=price * 0.98, risk_pct=self.params.risk_pct)
           return None
   ```
4. Registrala agregando su módulo a `_load_builtin_strategies` en `app/strategies/registry.py`.
5. **Reglas de oro:**
   - **No mires el futuro.** Usá solo `ctx.candles`. Nunca uses información posterior a la última vela.
   - Devolvé siempre un `stop_loss`: sin stop no hay riesgo definido y el tamaño se calcula distinto.
   - `Signal(action=...)` puede ser `"buy"`, `"sell"` o `"close"`. Con `take_profit` opcional.
   - Para datos extra (funding, open interest), declará `required_data = ("funding", "open_interest")` y leé las columnas `funding_rate` y `open_interest` de `ctx.candles`.
   - Calculá los indicadores sobre una **ventana corta** de velas (las últimas N), no sobre todo el historial: es mucho más rápido en el backtest.
   - Si la estrategia guarda estado interno, ojo: el backtester crea una instancia nueva por corrida.
6. **Testeala:** mirá `tests/test_new_strategies.py` como modelo (señales en escenarios armados, dirección, salidas, y una corrida dentro del motor donde el PnL debe cuadrar).
7. Subí `version` (atributo de clase) cada vez que cambie la lógica: queda registrada en cada backtest.
