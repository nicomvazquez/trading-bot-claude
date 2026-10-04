# Guía de estrategias

Una **estrategia** es un conjunto de reglas que, mirando las velas de un mercado, decide cuándo **comprar** (abrir un largo), **vender** (abrir un corto) o **cerrar**. El sistema trae siete. Todas se usan igual: se crea una *instancia* en **Estrategias**, o se prueba primero en **Backtesting**. La misma lógica corre en el backtest y en vivo, así que lo que ves en uno es lo que hace en el otro.

> **Aviso.** Ninguna de estas estrategias tiene una ventaja demostrada. Son puntos de partida para investigar. Un resultado bueno en un backtest no garantiza nada a futuro, y varias de ellas pierden dinero con sus parámetros por defecto (ver sección 17).

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

## 10. Price action: barrida de liquidez + cambio de estructura (MSS) + FVG — `sweep_mss`

**Idea.** Igual que la barrida ICT, el precio empuja por debajo (o por encima) de un nivel de liquidez para activar los stops, pero acá la entrada exige más pasos: que el precio **recupere** el nivel, que **cambie la estructura** (rompa el último swing en contra), que una **vela de desplazamiento** deje un FVG, y que el precio **vuelva** a ese FVG. Todo se define con velas cerradas, sin indicadores para generar la señal.

**Timeframes.** La instancia opera en su timeframe (1 minuto por defecto). El **contexto** (`context_minutes`, 5 minutos por defecto) se arma remuestreando esas mismas velas: swings, liquidez, barrida, MSS y FVG se detectan en el contexto, y el retroceso se busca en el timeframe de la instancia. `context_minutes` tiene que ser un múltiplo exacto del timeframe de la instancia.

**Reglas (largo; el corto es el espejo).**
1. **Liquidez:** mínimo del día anterior (UTC), swings confirmados y mínimos iguales (dos swings dentro de `equal_level_tolerance_pct`). Cada uno se puede apagar.
2. **Barrida:** una mecha perfora ese mínimo por una penetración entre `sweep_min_penetration_pct` y `sweep_max_penetration_pct`, y en `sweep_confirmation_bars` velas el cierre vuelve por encima del nivel.
3. **MSS:** después de la barrida, un cierre supera el último swing alto previo. Tiene que pasar en `mss_lookback` velas como máximo.
4. **Desplazamiento y FVG:** una vela de desplazamiento (cuerpo grande respecto del rango y del ATR, y volumen alto si está activado) deja un FVG alcista (el mínimo de la vela 3 queda por encima del máximo de la vela 1) de al menos `fvg_min_size_pct`. Mientras el precio no haya vuelto al FVG.
5. **Retroceso:** en el timeframe de la instancia, dentro de `retest_max_bars` velas, el precio vuelve al FVG y el cierre se sostiene por encima de su base. Es el **primer** retroceso; si antes el cierre cayó debajo de la zona, el setup se cancela.
6. **Entrada:** en el cierre de esa vela. **Stop** debajo de la mecha de la barrida (más `sl_buffer_pct`). **Take profit** a `tp_rr` veces el riesgo, o en la liquidez opuesta más cercana si `tp_at_opposite_liquidity` está activo y queda a más de 1R.

**Filtros opcionales.** Volatilidad (ATR actual frente al promedio, entre `atr_ratio_min` y `atr_ratio_max`), volumen relativo (RVOL de la vela de desplazamiento) y régimen (en tendencia alcista solo largos, en bajista solo cortos, en rango ambos). Todos apagados por defecto.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado por operación. |
| `context_minutes` | 5 | 2–240 | Timeframe de contexto (estructura y liquidez). Múltiplo del timeframe de la instancia. |
| `swing_n` | 2 | 1–10 | Velas de contexto a cada lado para confirmar un swing. |
| `liquidity_lookback` | 60 | 10–300 | Velas de contexto hacia atrás donde se busca la liquidez. |
| `use_prev_day_hl` | Sí | — | Usar el máximo/mínimo del día anterior como liquidez. |
| `use_swing_liquidity` | Sí | — | Usar los swings confirmados como liquidez. |
| `use_equal_hl` | Sí | — | Usar mínimos/máximos iguales como liquidez. |
| `equal_level_tolerance_pct` | 0.05 | 0–1 | Tolerancia para considerar iguales dos niveles (% del precio). |
| `sweep_min_penetration_pct` | 0.02 | 0–2 | Penetración mínima bajo el nivel (% del precio). |
| `sweep_max_penetration_pct` | 0.5 | 0–5 | Penetración máxima: si va más lejos, es ruptura y no barrida. |
| `sweep_confirmation_bars` | 2 | 1–10 | Velas de contexto para que el cierre recupere el nivel. |
| `mss_lookback` | 12 | 2–60 | Velas de contexto máximas desde la barrida hasta el MSS. |
| `use_displacement` | Sí | — | Exigir una vela de desplazamiento fuerte. |
| `displacement_min_body_ratio` | 0.6 | 0–1 | Cuerpo mínimo de la vela de desplazamiento (cuerpo / rango). |
| `displacement_atr_mult` | 1.0 | 0–5 | Cuerpo mínimo de la vela de desplazamiento (× ATR; 0 = sin filtro). |
| `fvg_min_size_pct` | 0.02 | 0–2 | Tamaño mínimo del FVG (% del precio). |
| `fvg_entry_midpoint` | No | — | Entrar en la mitad del FVG (si no, en el borde más cercano al precio). |
| `retest_max_bars` | 30 | 1–300 | Velas de la instancia para que el precio vuelva al FVG. |
| `sl_buffer_pct` | 0.05 | 0–2 | Margen del stop más allá de la mecha de la barrida (%). |
| `tp_rr` | 2.0 | 0.5–10 | Take profit como múltiplo del riesgo. |
| `tp_at_opposite_liquidity` | No | — | Tomar ganancia en la liquidez opuesta si está a más de 1R; si no, usa `tp_rr`. |
| `use_atr_filter` | No | — | Operar solo con la volatilidad dentro del rango permitido. |
| `atr_period` | 14 | 2–100 | Período del ATR (velas de contexto). |
| `atr_ratio_min` | 0.5 | 0–10 | ATR actual / ATR promedio: mínimo permitido. |
| `atr_ratio_max` | 2.0 | 0–10 | ATR actual / ATR promedio: máximo permitido. |
| `use_volume_filter` | No | — | Exigir volumen relativo alto en la vela de desplazamiento. |
| `rvol_period` | 20 | 2–200 | Velas de contexto para el volumen promedio (RVOL). |
| `rvol_min` | 1.5 | 0–10 | RVOL mínimo de la vela de desplazamiento. |
| `use_regime_filter` | No | — | En tendencia alcista solo largos, en bajista solo cortos; en rango, ambos. |

**Detalles.** Cada señal se puede inspeccionar: el campo `reason` del backtest y de la bitácora indica la liquidez barrida, el nivel del MSS y la zona del FVG. Un setup se usa una sola vez. Limitaciones de esta primera versión: no usa máximos/mínimos de sesión (solo día anterior), el take profit no tiene la opción de "swing previo" y el filtro de régimen es de encendido/apagado (sin modos "mean reversion" separados). Pensada para velas de 1 minuto con contexto de 5: genera **muchos menos setups** cuanto más filtros están activos, así que en períodos cortos las métricas son poco confiables.

## 11. Scalping: reversión a la media (Bollinger + RSI) — `scalp_bb_reversion`

**Idea.** Cuando el precio cierra fuera de las bandas de Bollinger y el RSI está en zona extrema, suele haber un estiramiento corto que vuelve a la media. La estrategia entra a favor de esa vuelta y apunta a la media. Pensada para **velas de 5 minutos** y para activos con rango amplio: opera mucho, así que cada operación tiene que dejar más que los costos.

**Reglas (largo; el corto es el espejo).**
1. El cierre queda **por debajo** de la banda inferior (media − `bb_std` × desvío de `bb_period` velas).
2. El RSI de `rsi_period` velas está por debajo de `rsi_oversold`.
3. La media está a al menos `min_target_pct` del precio (frente a los costos) y a al menos `min_reward_risk` veces el stop (la ganancia posible cubre el riesgo). Si no, se descarta.
4. **Entrada** al cierre de esa vela. **Stop** a `stop_atr_mult` × ATR. **Take profit** en la media.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado por operación. |
| `bb_period` | 20 | 5–100 | Velas de la media y de las bandas de Bollinger. |
| `bb_std` | 2.0 | 0.5–4 | Distancia de las bandas en desvíos estándar. |
| `rsi_period` | 14 | 2–50 | Período del RSI. |
| `rsi_oversold` | 25 | 1–50 | RSI por debajo de este valor habilita un largo. |
| `rsi_overbought` | 75 | 50–99 | RSI por encima de este valor habilita un corto. |
| `atr_period` | 14 | 2–100 | Período del ATR para el stop. |
| `stop_atr_mult` | 1.5 | 0.2–10 | Stop a esta cantidad de ATR de la entrada. |
| `min_target_pct` | 0.25 | 0–5 | Distancia mínima a la media para operar (%). |
| `min_reward_risk` | 1.0 | 0–5 | Distancia a la media como múltiplo del stop: el objetivo tiene que estar al menos así de lejos. |
| `use_trend_filter` | No | — | No revertir contra una tendencia clara del timeframe superior (EMA subiendo o bajando y cierre del lado correspondiente). |
| `trend_minutes` | 60 | 15–1440 | Timeframe superior del filtro de tendencia, en minutos. |
| `trend_ema_period` | 20 | 5–200 | Período de la EMA del timeframe superior. |
| `allow_long` | Sí | — | Permitir operaciones largas. |
| `allow_short` | Sí | — | Permitir operaciones cortas. |

**Detalles y advertencias.** Genera **muchas operaciones** (cientos en 120 días). Con los parámetros por defecto, en BTCUSDT y SOLUSDT de 5 minutos **pierde dinero**: la tasa de aciertos ronda el 25–30% y el costo de operar se come gran parte del capital. Con `min_reward_risk` en 1 se exige que el objetivo quede al menos tan lejos como el stop. Antes de confiar en la estrategia hay que verificar que esa condición mejore el resultado fuera de muestra, no solo subir la frecuencia.

## 12. Ruptura del rango de apertura por sesión — `session_breakout`

**Idea.** Cada sesión (Asia, Londres, Nueva York) marca su rango en las primeras velas. Si el precio sale de ese rango con un cierre, suele seguir en esa dirección durante la sesión. Pensada para **velas de 15 minutos** y para tener **1 a 3 operaciones por día**: como máximo una por sesión.

**Reglas.**
1. Al inicio de cada sesión habilitada (hora UTC) se forma su rango con las primeras `range_bars` velas.
2. El rango tiene que tener un tamaño entre `range_min_atr` y `range_max_atr` veces el ATR de 14 velas. Si es muy chico (sin movimiento) o muy grande (noticias), se descarta.
3. Durante las siguientes `entry_window_bars` velas, la primera vela que **cierra** fuera del rango define la operación: largo si cierra arriba, corto si cierra abajo. Si el precio ya había cerrado fuera antes, la sesión no se opera.
4. **Entrada** al cierre de esa vela. **Stop** del otro lado del rango (más `stop_buffer_pct`). **Take profit** a `rr_ratio` veces el riesgo.
5. Una operación por sesión y como máximo `max_trades_per_day` por día.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado por operación. |
| `use_asia` | Sí | — | Operar el rango de la sesión asiática. |
| `asia_start` | 0 | 0–23.5 | Inicio de la sesión asiática (hora UTC). |
| `use_london` | Sí | — | Operar el rango de la sesión de Londres. |
| `london_start` | 7 | 0–23.5 | Inicio de la sesión de Londres (hora UTC). |
| `use_newyork` | Sí | — | Operar el rango de la sesión de Nueva York. |
| `newyork_start` | 13 | 0–23.5 | Inicio de la sesión de Nueva York (hora UTC). |
| `range_bars` | 2 | 1–12 | Velas que forman el rango de cada sesión. |
| `entry_window_bars` | 8 | 1–48 | Velas después del rango en las que se acepta la ruptura. |
| `range_min_atr` | 0.5 | 0–10 | Rango mínimo como múltiplo del ATR. |
| `range_max_atr` | 3.0 | 0.1–20 | Rango máximo como múltiplo del ATR. |
| `rr_ratio` | 2.0 | 0.5–10 | Take profit como múltiplo del riesgo. |
| `stop_buffer_pct` | 0.05 | 0–2 | Margen del stop más allá del borde opuesto del rango (%). |
| `max_trades_per_day` | 3 | 1–10 | Máximo de operaciones por día (UTC). |

**Detalles y advertencias.** Con los parámetros por defecto, en SOLUSDT y BTCUSDT de 15 minutos, cada activo tuvo entre 1.7 y 2 operaciones por día, sin superar 3 en ningún día, pero **pierde dinero** (−39% y −42% en 120 días). La tasa de aciertos ronda el 33%, justo el punto de equilibrio para un riesgo/beneficio de 2:1 antes de costos. Para que funcione hace falta que la tasa de aciertos sea mayor o que el objetivo sea más amplio, y eso hay que verificarlo fuera de muestra. Las horas están fijas en UTC: la apertura real de Londres y Nueva York se corre una hora con el cambio de horario.

## 13. Tendencia intradía con sesgo diario (Londres / Nueva York) — `daytrend_session`

**Idea.** Opera a favor de la tendencia del día. La tendencia diaria marca la dirección; dentro de las ventanas de Londres y Nueva York se espera un retroceso a la media de 15 minutos y se entra cuando el precio vuelve a cerrar a favor. Pensada para **velas de 15 minutos** y para **hasta 2 operaciones por día**.

**Reglas (largo; el corto es el espejo).**
1. **Sesgo diario:** en el último día completo, el cierre queda sobre la EMA diaria y la EMA sube → solo largos. El caso opuesto → solo cortos. Sin tendencia clara, no opera ese día.
2. **Ventanas:** cada sesión habilitada abre una ventana de `entry_window_bars` velas de 15 minutos a partir de su hora de inicio (UTC).
3. **Retroceso:** dentro de la ventana, en tendencia alcista el precio baja hasta la EMA de 15 minutos y una vela cierra por encima de la EMA y en alza. Esa es la entrada, al cierre de la vela.
4. **Stop:** debajo del último swing bajo confirmado de las últimas `swing_lookback` velas, con un margen de `stop_buffer_pct`. **Take profit** a `rr_ratio` veces el riesgo.
5. Una operación por ventana y como máximo `max_trades_per_day` por día (UTC).

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado por operación. |
| `daily_ema_period` | 20 | 5–200 | EMA diaria que define la tendencia del día. |
| `pullback_ema_period` | 20 | 5–200 | EMA de 15 minutos a la que se espera el retroceso. |
| `use_london` | Sí | — | Operar en la ventana de Londres. |
| `london_start` | 7 | 0–23.5 | Inicio de la ventana de Londres (hora UTC). |
| `use_newyork` | Sí | — | Operar en la ventana de Nueva York. |
| `newyork_start` | 13 | 0–23.5 | Inicio de la ventana de Nueva York (hora UTC). |
| `entry_window_bars` | 16 | 2–96 | Velas de 15 minutos de cada ventana en las que se busca el retroceso. |
| `swing_n` | 2 | 1–10 | Velas a cada lado para confirmar el swing del stop. |
| `swing_lookback` | 96 | 5–300 | Velas hacia atrás donde buscar el swing del stop (96 = un día). |
| `stop_buffer_pct` | 0.05 | 0–2 | Margen del stop más allá del swing (%). |
| `rr_ratio` | 2.0 | 1.5–10 | Take profit como múltiplo del riesgo. |
| `max_trades_per_day` | 2 | 1–10 | Máximo de operaciones por día (UTC). |

**Detalles.** Si no hay un swing confirmado dentro de `swing_lookback`, no opera: un stop sin estructura no se pone. Los valores por defecto son un punto de partida, no una configuración validada: hay que medirla con datos de un año, dentro y fuera de muestra, antes de confiar en ella.

## 14. Ruptura de máximos y mínimos del día anterior (Londres / Nueva York) — `prev_day_breakout`

**Idea.** El máximo y el mínimo del día anterior son niveles que muchos operadores miran. Cuando el precio los rompe con un cierre durante una sesión activa, puede seguir en esa dirección. La estrategia opera solo la primera ruptura de cada nivel en el día, y solo dentro de las ventanas de Londres y Nueva York. Pensada para **velas de 15 minutos** y para **1 a 3 operaciones por día**.

**Reglas.**
1. **Niveles:** máximo y mínimo del día UTC anterior completo.
2. **Disparo:** la primera vela de 15 minutos del día que cierra fuera de un nivel, dentro de una ventana de sesión. Si el precio ya había cerrado fuera de ese nivel antes en el día, ese nivel no se opera.
3. **Entrada** al cierre de esa vela. **Stop** a `stop_atr_mult` × ATR de 14 velas de la entrada. **Take profit** a `rr_ratio` veces el riesgo.
4. Cada nivel se opera como máximo una vez por día, y el total no supera `max_trades_per_day`.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `risk_pct` | 1.0 | 0.1–10 | % del capital arriesgado por operación. |
| `use_london` | Sí | — | Operar en la ventana de Londres. |
| `london_start` | 7 | 0–23.5 | Inicio de la ventana de Londres (hora UTC). |
| `use_newyork` | Sí | — | Operar en la ventana de Nueva York. |
| `newyork_start` | 13 | 0–23.5 | Inicio de la ventana de Nueva York (hora UTC). |
| `entry_window_bars` | 16 | 1–96 | Velas de 15 minutos de cada ventana en las que se acepta la ruptura. |
| `stop_atr_mult` | 1.5 | 0.2–10 | Stop a esta cantidad de ATR de la entrada. |
| `rr_ratio` | 2.0 | 0.5–10 | Take profit como múltiplo del riesgo. |
| `max_trades_per_day` | 3 | 1–10 | Máximo de operaciones por día (UTC). |

**Detalles.** Un día con un movimiento fuerte en la sesión anterior tiene niveles lejanos y puede no generar ninguna operación; un día tranquilo puede generar una sola. Los valores por defecto son un punto de partida: hay que medirlos con datos de un año, dentro y fuera de muestra, antes de confiar en ellos.

## 15. Liquidez + barrida + BOS + retest (1 h / 15 m / 5 m) — `liquidity_bos_retest`

**Idea.** Una barrida de liquidez (el precio perfora un nivel, lo recupera y cierra de nuevo a favor) indica que los stops de un lado fueron activados. Si después la estructura cambia (un cierre supera el último swing contrario), y el precio vuelve a probar el nivel roto (retest) y una vela de confirmación lo sostiene, la probabilidad de continuidad es mayor. Pocas operaciones, selección estricta.

**Timeframes.** Contexto (1 hora): tendencia por máximos y mínimos crecientes (HH/HL) o decrecientes (LH/LL). Estructura (15 minutos): niveles, barridas y BOS. Ejecución (5 minutos, el timeframe de la instancia): retest y confirmación. Cada timeframe tiene que ser múltiplo exacto del anterior.

**Secuencia (largo; el corto es el espejo).**
1. **Liquidez:** mínimo del día anterior (UTC), mínimo del día actual, swings confirmados y mínimos iguales. Cada fuente se activa o desactiva por separado.
2. **Barrida:** la mecha perfora el nivel por una penetración entre `sweep_min_penetration_pct` y `sweep_max_penetration_pct`, y en `sweep_confirmation_bars` velas el cierre recupera el nivel.
3. **BOS:** después de la barrida, un cierre supera el último swing alto anterior a la barrida (por encima del nivel barrido), con al menos `mss_min_break_pct` de distancia, dentro de `mss_lookback` velas.
4. **Retest:** el precio vuelve a la zona del swing roto (± `retest_tolerance_pct`) dentro de `max_retest_bars` velas de ejecución. Si el precio cierra del lado contrario antes de volver, el setup se invalida.
5. **Confirmación:** una vela de ejecución toca la zona y cierra fuera de ella a favor (si `require_confirmation_candle` está activo). Entrada al cierre de esa vela.
6. **Stop:** debajo del extremo de la barrida (más `stop_buffer_pct`). **Objetivo:** la liquidez opuesta más cercana que cumple `minimum_risk_reward`. Si ninguna la cumple, no se opera.

**Filtros opcionales.** Contexto (solo a favor de la tendencia, o también reversiones), sesión horaria (UTC), volumen relativo (RVOL) y volatilidad (ATR actual frente al promedio). Todos apagados por defecto, salvo la relación riesgo/beneficio y los límites de operaciones.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `context_minutes` | 60 | 15–1440 | Timeframe de contexto (tendencia), en minutos; múltiplo del timeframe de estructura |
| `structure_minutes` | 15 | 5–240 | Timeframe de estructura (swings, sweeps y BOS), en minutos; múltiplo del timeframe de ejecución |
| `swing_n` | 2 | 1–10 | Velas de estructura a cada lado para confirmar un swing |
| `swing_lookback` | 60 | 10–300 | Velas de estructura hacia atrás donde se buscan niveles de liquidez |
| `regime_filter_enabled` | No | — | Usar el contexto para filtrar la dirección |
| `allow_trend_trades` | Sí | — | Con filtro activo: operar a favor de la tendencia del contexto |
| `allow_reversals` | No | — | Con filtro activo: permitir operaciones contra la tendencia y en rango |
| `use_previous_day_levels` | Sí | — | Usar máximo/mínimo del día anterior (UTC) como liquidez |
| `use_session_levels` | Sí | — | Usar máximo/mínimo del día actual (UTC) como liquidez |
| `use_swing_levels` | Sí | — | Usar swings confirmados como liquidez |
| `use_equal_levels` | Sí | — | Usar máximos/mínimos iguales como liquidez |
| `liquidity_tolerance_pct` | 0.05 | 0–1 | Tolerancia para considerar iguales dos niveles (% del precio) |
| `sweep_min_penetration_pct` | 0.02 | 0–2 | Penetración mínima bajo/sobre el nivel (% del precio) |
| `sweep_max_penetration_pct` | 0.5 | 0–5 | Penetración máxima: si va más lejos no es barrida sino ruptura |
| `sweep_confirmation_bars` | 2 | 1–10 | Velas de estructura para que el cierre recupere el nivel barrido |
| `mss_lookback` | 12 | 2–60 | Velas de estructura máximas desde la barrida hasta el BOS |
| `mss_min_break_pct` | 0 | 0–2 | Distancia mínima de cierre sobre el swing roto (% del precio) |
| `retest_tolerance_pct` | 0.05 | 0–2 | Ancho de la zona de retest alrededor del nivel roto (% del precio) |
| `max_retest_bars` | 36 | 1–300 | Velas de ejecución para que llegue el retest; si no, se cancela el setup |
| `require_confirmation_candle` | Sí | — | Exigir una vela de confirmación alcista (bajista en cortos) que cierre fuera de la zona |
| `volume_filter_enabled` | No | — | Exigir volumen relativo mínimo en la vela de confirmación |
| `volume_period` | 20 | 2–200 | Velas de ejecución para el volumen promedio (RVOL) |
| `minimum_rvol` | 1.5 | 0–10 | RVOL mínimo de la vela de confirmación |
| `volatility_filter_enabled` | No | — | Operar solo con volatilidad dentro del rango permitido |
| `atr_period` | 14 | 2–100 | Período del ATR de ejecución |
| `minimum_volatility` | 0.5 | 0–10 | ATR actual / ATR promedio: mínimo permitido |
| `maximum_volatility` | 2 | 0–10 | ATR actual / ATR promedio: máximo permitido |
| `stop_buffer_pct` | 0.05 | 0–2 | Margen del stop más allá del extremo del sweep (%) |
| `minimum_risk_reward` | 2 | 0.5–10 | Relación mínima entre el objetivo en liquidez y el riesgo; si no se cumple, no se opera |
| `max_trades_per_day` | 2 | 1–10 | Máximo de operaciones por día (UTC) |
| `max_long_trades_per_day` | 2 | 0–10 | Máximo de largos por día (UTC) |
| `max_short_trades_per_day` | 2 | 0–10 | Máximo de cortos por día (UTC) |
| `cooldown_after_trade_bars` | 12 | 0–500 | Velas de ejecución de espera después de cada entrada |
| `session_filter_enabled` | No | — | Operar solo dentro de la ventana horaria indicada (hora UTC) |
| `session_start_hour` | 0 | 0–24 | Inicio de la ventana horaria (UTC) |
| `session_end_hour` | 24 | 0–24 | Fin de la ventana horaria (UTC) |
| `risk_pct` | 1 | 0.1–10 | % de equity a arriesgar por operación |

**Detalles y limitaciones.** Los niveles de sesión son el máximo y mínimo del día UTC (no hay sesiones de Asia o Londres separadas). El filtro de sesión usa horas UTC fijas. No hay cooldown después de pérdidas: el motor de la estrategia no conoce el resultado de cada operación. Para ver por qué no se tomó una operación hay que revisar la lógica de cada condición; la estrategia no registra motivos de rechazo todavía. Los valores por defecto son un punto de partida: hay que medirlos con datos de un año antes de confiar en ellos.

## 16. Cruce de EMAs con confirmación (tendencia, ADX, volumen, volatilidad) — `ema_crossover`

**Idea.** El cruce de dos medias exponenciales indica un cambio de impulso, pero en mercados laterales produce muchas señales falsas. Esta estrategia exige que el cruce ocurra a favor de la tendencia de fondo (una media de 200 períodos en el contexto de 15 minutos), con fuerza suficiente (ADX), volumen y volatilidad razonables, y una vela de confirmación.

**Timeframes.** Ejecución: el timeframe de la instancia (5 minutos por defecto). Contexto: la EMA de tendencia se calcula sobre velas de `context_minutes` (15 minutos) ya cerradas. El contexto tiene que ser múltiplo exacto del timeframe de ejecución.

**Secuencia (largo; el corto es el espejo).**
1. **Cruce:** la EMA rápida cruza hacia arriba a la lenta en velas de ejecución cerradas.
2. **Tendencia:** el precio está sobre la EMA de tendencia (contexto) y esa EMA sube en los últimos `trend_slope_lookback` períodos, por encima de `minimum_trend_slope`.
3. **Filtros opcionales:** ADX mínimo, volumen relativo mínimo y volatilidad (ATR actual frente al promedio) dentro del rango permitido. Todos apagados por defecto, salvo la tendencia.
4. **Confirmación:** por defecto, la entrada es al cierre de la vela del cruce. Con `next_candle_confirmation`, se espera la vela siguiente y debe seguir en la misma dirección. Con `pullback_entry_enabled`, se espera un retroceso que toque la EMA rápida dentro de `max_pullback_bars` velas, sin que la EMA lenta se rompa, y una vela que vuelva a cerrar a favor.
5. **Salidas:** stop a `stop_loss_atr_multiplier` × ATR; take profit a `take_profit_rr` veces el riesgo; cierre por tiempo si la operación dura más de `max_bars_in_trade` velas.
6. **Límites:** `max_trades_per_day` (20 por defecto, para frecuencia de scalping) y espera de `cooldown_after_trade_bars` velas (3 por defecto) después de cada entrada.

**Parámetros.**

| Parámetro | Por defecto | Rango | Qué hace |
|---|---|---|---|
| `context_minutes` | 15 | 5–240 | Timeframe de contexto (tendencia de la EMA larga), en minutos; múltiplo del timeframe de ejecución |
| `fast_ema_period` | 9 | 2–100 | Período de la EMA rápida |
| `slow_ema_period` | 21 | 3–200 | Período de la EMA lenta |
| `trend_ema_period` | 200 | 20–500 | Período de la EMA de tendencia (timeframe de contexto) |
| `trend_filter_enabled` | Sí | — | Operar largos solo con precio sobre la EMA de tendencia, y cortos solo debajo |
| `trend_slope_enabled` | Sí | — | Exigir pendiente de la EMA de tendencia en la misma dirección |
| `trend_slope_lookback` | 10 | 1–100 | Velas de contexto para medir la pendiente de la EMA de tendencia |
| `minimum_trend_slope` | 0 | -5–5 | Pendiente mínima de la EMA de tendencia en el lookback (%) |
| `adx_filter_enabled` | No | — | Operar solo con ADX por encima del mínimo (fuerza de tendencia) |
| `adx_period` | 14 | 2–100 | Período del ADX |
| `adx_minimum` | 22 | 5–60 | ADX mínimo para operar |
| `volume_filter_enabled` | No | — | Exigir volumen relativo mínimo en la vela de entrada |
| `volume_period` | 20 | 2–200 | Velas para el volumen promedio (RVOL) |
| `minimum_relative_volume` | 1 | 0–10 | RVOL mínimo de la vela de entrada |
| `volatility_filter_enabled` | No | — | Operar solo con volatilidad dentro del rango permitido |
| `atr_period` | 14 | 2–100 | Período del ATR |
| `minimum_atr_ratio` | 0.5 | 0–10 | ATR actual / ATR promedio: mínimo permitido |
| `maximum_atr_ratio` | 2 | 0–10 | ATR actual / ATR promedio: máximo permitido |
| `next_candle_confirmation` | No | — | Entrar en la vela siguiente al cruce, si mantiene la dirección (si no, entra al cierre del cruce) |
| `pullback_entry_enabled` | No | — | Entrar después de un retroceso a la EMA rápida dentro de la ventana, en vez de entrar en el cruce |
| `max_pullback_bars` | 12 | 1–100 | Velas de ejecución después del cruce para que llegue el retroceso |
| `pullback_tolerance_pct` | 0.1 | 0–2 | Tolerancia para tocar la EMA rápida en el retroceso (%) |
| `stop_loss_atr_multiplier` | 1.2 | 0.2–10 | Stop a N × ATR de la entrada |
| `take_profit_rr` | 2 | 0.5–10 | Take profit como múltiplo del riesgo (R) |
| `max_bars_in_trade` | 48 | 1–1000 | Velas de ejecución máximas dentro de una operación; luego se cierra por tiempo |
| `max_trades_per_day` | 20 | 1–100 | Máximo de operaciones por día (UTC) |
| `cooldown_after_trade_bars` | 3 | 0–500 | Velas de ejecución de espera después de cada entrada |
| `session_filter_enabled` | No | — | Operar solo dentro de la ventana horaria indicada |
| `session_start_hour` | 0 | 0–24 | Inicio de la ventana horaria (hora local de session_timezone) |
| `session_end_hour` | 24 | 0–24 | Fin de la ventana horaria (hora local de session_timezone) |
| `session_timezone` | UTC | — | Zona horaria de la ventana (por ejemplo UTC o America/Argentina/Buenos_Aires) |
| `risk_pct` | 1 | 0.1–10 | % de equity a arriesgar por operación |

**Limitaciones de esta versión.** No hay modos de stop por swing ni de take profit por liquidez: solo ATR y R fijo. El cooldown después de pérdidas no está implementado, porque la estrategia no recibe el resultado de cada operación. No hay salida por señal contraria. El motivo de cada rechazo queda en el log de depuración y en el atributo `last_rejection`, pero no se guarda en la base de datos. Los valores por defecto son un punto de partida, no una configuración validada.

## 17. Resultados de referencia de nuestras pruebas

Esta tabla sale de `scripts/validate_strategies.py`: para cada una de las siete estrategias, con sus **parámetros por defecto** (sin re-optimizar) sobre **BTCUSDT**, corre un backtest completo de ~700 días, un **walk-forward** con parámetros fijos (ventanas de 180 días de entrenamiento / 60 de test, no solapadas) y una simulación **Monte Carlo** (bootstrap, 2000 corridas). Es mucho más exigente que un solo backtest: el walk-forward mide si la estrategia se sostiene en tramos que nunca vio, y Monte Carlo estima qué tan dependiente del orden de las operaciones es el resultado.

| Estrategia | Timeframe | Operaciones | Retorno (período completo) | Retorno walk-forward (OOS) | Ventanas ganadoras | Monte Carlo: prob. de pérdida | Monte Carlo: prob. de ruina (DD > 20%) |
|---|---|---|---|---|---|---|---|
| **Funding + OI** | 4 h | 25 | **+4,0%** | **+1,2%** | 37,5% (3/8) | 20% | **0%** |
| Donchian | 1 h | 318 | −9,8% | −11,9% | 50% (4/8) | 63,5% | 91,4% |
| ICT (barrida + FVG) | 15 min | 134 | −18,8% | −8,0% | 37,5% (3/8) | 90,75% | 73,8% |
| Reversión RSI | 15 min | 866 | −23,5% | −24,8% | 12,5% (1/8) | 96,75% | 82,1% |
| Cruce de medias (SMA) | 15 min | 948 | −33,0% | −31,3% | 25% (2/8) | 95,6% | 96,25% |
| Retroceso a favor de tendencia | 1 h | 238 | −32,2% | −32,5% | 25% (2/8) | 96,3% | 95,35% |
| Compresión y expansión | 1 h | 362 | −41,8% | −40,5% | 25% (2/8) | 95,5% | **99,3%** |

**Cómo leer esta tabla:** de las siete, solo **Funding + OI** se sostiene fuera de muestra (walk-forward positivo, 0% de probabilidad de ruina), aunque con apenas 25 operaciones en ~700 días — por debajo del umbral de 30 que el propio motor de Monte Carlo considera confiable, así que es prometedora pero todavía no es una confirmación. Las otras seis, **incluidas RSI y SMA**, no se sostienen con sus parámetros de fábrica: probabilidad de pérdida por encima del 60% en todas, y mayor al 90% en la mitad de ellas. Donchian e ICT son las "menos malas" del resto. Una hipótesis para RSI y SMA: operan muy seguido para estrategias tan simples (más de 1 operación por día en 15 minutos), lo que sugiere que podrían estar perdiendo contra las comisiones y el ruido del mercado lateral más que por ausencia total de señal — pero es una hipótesis, no una conclusión: haría falta re-optimizar sus parámetros y volver a validar fuera de muestra, no solo cambiarles la fe.

**Esta tabla es un punto de partida para re-optimizar, no un veredicto final.** El timeframe de cada estrategia también es una elección (no hay uno "oficial" en el código): se puede repetir el análisis con otros valores. Para volver a correrla:

```bash
docker compose exec -e PYTHONPATH=/app app python scripts/validate_strategies.py
```

**Lo importante es el método:** probar, validar fuera de muestra y con walk-forward, y descartar lo que no aguanta. Ver la **Guía del backtester**.

---

## 18. Cómo crear tu propia estrategia (para desarrolladores)

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
       style = "Tendencia"       # opcional: tag corto para el catálogo (Tendencia, Reversión, Ruptura, Contrarian...)
       default_timeframe = "60"  # opcional: precarga este timeframe al crear una instancia nueva (por defecto "15")

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
