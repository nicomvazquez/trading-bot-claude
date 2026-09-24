# Guía del backtester

El backtester prueba una estrategia sobre **datos históricos reales** de Bybit y te dice cuánto habría ganado o perdido. Pero lo más importante que hace no es dar un número de ganancia: es ayudarte a saber **cuánto confiar** en ese número.

> **Un backtest no predice el futuro.** Mide cómo le habría ido a una regla en el pasado. Casi cualquier estrategia se puede ajustar hasta que luzca bien en el pasado (*sobreajuste*). Por eso esta guía insiste tanto en las pruebas de robustez: sirven para descartar lo que no aguanta.

## 1. Índice

2. Qué hace y qué no hace
3. Flujo de trabajo
4. La configuración, campo por campo
5. Cómo simula una operación (las reglas del motor)
6. Cómo leer los resultados, pestaña por pestaña
7. Cómo interpretar las métricas
8. Ejemplo comentado
9. Limitaciones conocidas
10. Reproducibilidad y descarga

---

## 2. Qué hace y qué no hace

**Hace:**
- Simula la estrategia vela por vela, con comisiones, slippage, spread y funding.
- Usa exactamente la **misma lógica de la estrategia que se usa en vivo**.
- Mide performance, riesgo y calidad estadística, y **avisa cuando un resultado no es confiable**.
- Prueba la robustez: Monte Carlo, sensibilidad a parámetros, fuera de muestra y walk-forward.
- Mide la fragilidad frente a costos y condiciones adversas: sensibilidad a costos y stress tests.
- Guarda cada corrida con su configuración completa, operaciones y curva de capital, y permite verla de nuevo, clonarla, compararla con otras y descargarla.

**No hace:**
- No garantiza resultados futuros.
- No simula la liquidación de la posición ni el margen (ver sección 9).
- No modela impacto de mercado ni latencia.

---

## 3. Flujo de trabajo

1. En **Backtesting**, elegí estrategia, símbolo, timeframe, el **rango de fechas** y el capital.
2. Ajustá los parámetros de la estrategia (sección A) y, **muy importante**, los **costos realistas** (sección C).
3. **Ejecutar backtest.** La primera vez descarga las velas de Bybit (puede tardar); después usa una caché.
4. Leé primero los **avisos** de la pestaña Overview, antes de mirar el retorno.
5. Recorré Equity, Trades y Risk para entender *cómo* se consiguió el resultado.
6. Corré **Monte Carlo** para medir cuánto depende de la suerte.
7. Probá la **robustez**: Sensibilidad, Fuera de muestra y Walk-forward.
8. Sometela a **costos peores y condiciones adversas** en la pestaña Stress Test.
9. Compará variantes en **History**.
10. Si sigue de pie después de todo eso, recién ahí tiene sentido probarla en Demo.

---

## 4. La configuración, campo por campo

### Mercado y capital

| Campo | Qué es |
|---|---|
| Estrategia | La estrategia a probar. Sus parámetros aparecen en la sección A. |
| Símbolo | El par de Bybit (por ejemplo `BTCUSDT`). |
| Timeframe | Tamaño de cada vela: 1, 5, 15 minutos, 1 hora, 4 horas o 1 día. |
| Rango de fechas (hora argentina) | El tramo de historia a simular, elegido en un calendario (desde y hasta, ambos días incluidos; los días son de la hora argentina: cada día empieza a las 00:00 de Argentina, que son las 03:00 UTC), o con los atajos de 30 días, 90 días, 6 meses, 1 año y 2 años (cuentan hacia atrás desde hoy). Por defecto, los últimos 90 días. Podés elegir **cualquier período pasado**, por ejemplo un mercado bajista concreto o el año que no usaste para diseñar la estrategia. Mínimo 7 días y máximo 1500. No se pueden elegir fechas futuras. **Más es mejor**: con pocos meses muchas estrategias generan pocas operaciones y no se puede concluir nada; usá al menos 1 año. Las primeras velas del rango se usan para *calentar* los indicadores de la estrategia (por eso el período simulado arranca unos días después de la fecha elegida). Si el símbolo no existía al comienzo del rango, un aviso te dice desde qué fecha hay datos. |
| Capital inicial (USD) | Con cuánto arranca la simulación (por defecto 1000). |

**Elegir el período con criterio.** El rango que elegís puede cambiar el veredicto de una estrategia: una que gana en un año alcista puede perder en uno lateral. Probala en **varios períodos distintos** (uno alcista, uno bajista, uno lateral) y reservá un tramo que no hayas mirado para confirmar al final. Elegir el rango donde mejor le fue es una forma de sobreajuste.

### A · Parámetros de la estrategia
Los propios de la estrategia (ver la Guía de estrategias). Cada uno tiene su descripción.

### B · Riesgo

| Campo | Qué es |
|---|---|
| Cálculo del tamaño | **Por riesgo hasta el stop** (por defecto): `cantidad = capital × riesgo% ÷ distancia al stop`. O **nocional fijo**: un % fijo del capital, sin importar el stop. |
| Riesgo por operación (%) | Si lo completás, **reemplaza** al de la estrategia. Vacío = usa el de la estrategia. |
| Nocional (% del equity) | Solo para nocional fijo. |
| Apalancamiento máx. (x) | **Tope** de exposición: el nocional no supera capital × este valor (1 a 125x, por defecto 10). No define el tamaño. |
| Posición máx. (% del equity) | Tope adicional opcional del tamaño. |
| El riesgo incluye costos | Suma comisiones y slippage a la distancia al stop al calcular el tamaño, para que el riesgo real (con costos) sea el pedido. |

El backtester opera **una posición a la vez** (no acumula posiciones).

### C · Ejecución

| Campo | Qué es |
|---|---|
| Modelo de ejecución | **Apertura de la vela siguiente** (recomendado): la señal nace al cierre de una vela y se ejecuta en la apertura de la siguiente, como pasa en la realidad. **Cierre de la misma vela** es optimista: asume ejecutar al precio que ya generó la señal. |
| Tipo de orden de entrada | **Market (taker):** se ejecuta siempre, pagando comisión taker, slippage y medio spread. **Limit (maker):** se deja una orden al precio de cierre de la señal; solo se ejecuta si el precio la alcanza dentro de su vigencia. Las que vencen sin ejecutarse son operaciones perdidas y se informan. |
| Vigencia orden limit (velas) | Cuántas velas sigue viva la orden limit. |
| Fee taker (%) | Comisión de las órdenes de mercado y de los stop-loss. Por defecto **0,055%** por lado (la de Bybit). |
| Fee maker (%) | Comisión de las órdenes límite (take-profit y entradas limit). Por defecto en pantalla **0,02%**. |
| Slippage (bps) | Empeoramiento del precio por el movimiento entre que decidís y ejecutás. 1 bps = 0,01%. |
| Spread (bps) | Diferencia entre compra y venta. En una orden de mercado se paga **la mitad**. |
| Funding | **Sin funding**, **Constante** (un % por cada 8 horas) o **Histórico real** (el de Bybit, recomendado). |
| Resolver stop/TP ambiguos con timeframe menor | Cuando en una misma vela se tocan el stop y el take-profit, usa velas más chicas para saber cuál fue primero (ver sección 5). |

**Configuración realista sugerida para BTCUSDT o ETHUSDT:** modelo *apertura de la vela siguiente*, orden *market*, fee taker 0,055%, fee maker 0,02%, slippage de 2 a 5 bps, spread de 1 a 2 bps, funding *histórico* y resolución intravela activada. Para monedas menos líquidas, usá valores mayores. **Con slippage, spread y funding en cero, los resultados son optimistas** (el sistema lo avisa).

### D · Validación

| Campo | Qué es |
|---|---|
| Método Monte Carlo | Shuffle, Bootstrap o Block bootstrap (ver la pestaña Monte Carlo). |
| Simulaciones | Cuántas remuestras (por defecto 1000). |
| Semilla | Número que fija el azar para que el resultado sea **reproducible** (por defecto 42). Vacío = aleatorio. |
| Tamaño de bloque | Solo para Block bootstrap. |
| Umbral de drawdown (%) | Para estimar la probabilidad de sufrir una caída mayor a ese porcentaje (por defecto 20%). |

---

## 5. Cómo simula una operación (las reglas del motor)

Entender estas reglas evita malas interpretaciones.

**Cuándo se decide y cuándo se ejecuta.** La estrategia mira la vela **cuando cierra** y, si hay señal, la orden se ejecuta en la **apertura de la vela siguiente**. Nunca se usa información posterior a la vela que se está evaluando.

**Costos.**
- **Entrada y salida con orden de mercado:** se ejecutan al precio de apertura, empeorado por el slippage y medio spread, y pagan comisión taker.
- **Stop-loss:** se trata como orden de mercado (taker con slippage). Si la vela **abre más allá del stop** (un *gap*), se ejecuta al precio de apertura, **no** al del stop: pierde más de lo planeado, como en la realidad.
- **Take-profit:** se trata como orden límite (maker, sin slippage).
- **Funding:** se cobra o paga cada 8 horas (00:00, 08:00 y 16:00 UTC, es decir 21:00, 05:00 y 13:00 en hora argentina) mientras hay posición. Si es positivo, pagan los largos.
- **PnL neto = PnL bruto − comisiones de entrada y salida ± funding.** Todas las métricas se calculan sobre el neto.

**Stop y take-profit en la misma vela.** Con solo velas no se sabe cuál se tocó primero. Por defecto se asume **el stop primero** (lo conservador). Si activás la resolución intravela, se descargan velas de un timeframe menor (5 min → 1 min, 15 min → 1 min, 1 h → 5 min, 4 h → 15 min, 1 día → 1 hora) para determinar el orden real; los casos que no se pueden resolver se informan.

**Tamaño.** Se calcula igual que en vivo (ver la sección B), redondeado hacia abajo; nunca supera el tope de apalancamiento.

**Una posición a la vez.** Mientras hay una operación abierta, las señales de entrada en la misma dirección se ignoran (y se cuentan como *señales ignoradas* en los avisos). Una señal contraria cierra y da vuelta la posición (*flip*).

**Fin del backtest.** Si queda una posición abierta al final de los datos, se cierra al último precio, se marca como *fin del backtest* y **cuenta como operación**.

**Datos.** **Horarios.** Todas las fechas y horas que ves (operaciones, gráficos, períodos, fecha de corte) están en **hora argentina (UTC-3)**. Los datos se guardan internamente en UTC y se convierten al mostrarlos.

**Datos.** Las velas vienen de la API pública de Bybit (mercado real) y se guardan en la base como caché. La última vela, si todavía se estaba formando, se excluye. Antes de simular se revisa la calidad: velas duplicadas, inválidas o huecos en el tiempo se corrigen o se informan en los avisos.

---

## 6. Cómo leer los resultados, pestaña por pestaña

Arriba de todo, una fila de etiquetas resume la corrida: estrategia y versión, símbolo, timeframe, período, capital, comisiones, slippage y spread, funding y apalancamiento. **Comprobá siempre estas etiquetas**: te dicen bajo qué supuestos se obtuvo el resultado.

### 6.1 Overview

**Lo primero: los avisos.** Un panel muestra advertencias (naranjas = importantes, azules = informativas). Por ejemplo:

| Aviso | Qué significa |
|---|---|
| *Solo N operaciones: muestra insuficiente* (menos de 10) | Las métricas no son estadísticamente significativas. No concluyas nada. |
| *N operaciones: muestra chica* (10 a 29) | Alta incertidumbre. |
| *Período muy corto* (menos de 30 días) | Las métricas anualizadas no son representativas. |
| *CAGR y Calmar se anualizan a partir de N días* (menos de un año) | Extrapolan el ritmo del período a un año: no son representativos. |
| *Sin slippage, spread ni funding* | Resultado optimista. Agregá los costos en la sección C. |
| *Ejecución en el cierre de la misma vela* | Modelo optimista: no refleja la realidad. |
| *N operaciones fueron limitadas por el apalancamiento* | Su riesgo real fue menor al configurado. |
| *Señales ignoradas* | La estrategia quiso operar mientras ya tenía posición. |
| *N órdenes limit vencieron sin ejecutarse* | Operaciones que se perdieron por no alcanzarse el precio. |
| *N velas con stop y take-profit en la misma vela…* | No se pudo determinar el orden real; se asumió el stop. |
| *El drawdown más largo no se recuperó* | Terminó el período todavía por debajo de su máximo. |
| *Había una posición abierta al final* | Se cerró a la fuerza al último precio. |
| Avisos de calidad de datos | Velas descartadas, duplicadas o huecos. |

**Luego, las tarjetas:**
- **Retorno total** (la grande): ganancia o pérdida sobre el capital inicial, con el capital final y el CAGR.
- **Drawdown máx.** y su duración.
- **Sharpe**, con Sortino y Calmar debajo.
- **Profit factor**, con la ganancia y la pérdida promedio.
- **Win rate**, con la cantidad de ganadoras y perdedoras.
- **Expectativa por operación:** cuánto gana o pierde en promedio cada operación.
- **Tiempo en mercado** y su reparto entre long y short.

Pasá el mouse por el ícono ℹ de cada tarjeta para ver su definición.

### 6.2 Equity

- **Equity y drawdown**, en dos gráficos separados: arriba el **capital** a lo largo del tiempo; abajo el **drawdown** (cuánto cayó desde su máximo). Los marcadores son: **▲ entrada long**, **▼ entrada short**, **● salida ganadora** y **✖ salida perdedora**.
- El capital se mide **al cierre de cada vela**, con el PnL no realizado incluido.
- **Tocá un marcador** de entrada o salida y aparece debajo el detalle de esa operación (precios, costos, PnL, motivo).
- También hay un gráfico de **precio con las operaciones** marcadas, para ver dónde entró y salió la estrategia.

**Qué buscar:** una curva que sube de forma relativamente estable es mejor que una que llega al mismo resultado con saltos enormes. Un tramo largo bajo el agua indica una estrategia difícil de sostener. Si casi toda la ganancia viene de uno o dos saltos, el resultado depende de pocas operaciones.

### 6.3 Trades

- **Filtros:** Todas, Long, Short, Ganadoras, Perdedoras.
- **Tabla de operaciones:** entrada, salida, lado, precios, tamaño, nocional, PnL bruto, comisiones, funding, PnL neto, retorno, duración, motivo de salida y motivo de entrada. Se ordena por la mayoría de las columnas.
- **Long vs Short:** compara los dos lados. Si uno gana y el otro pierde, quizás conviene desactivar ese lado.
- **Distribución del PnL neto** y **de la duración:** histogramas con la mediana marcada.

**Qué buscar:** la forma de la distribución. Muchas pérdidas chicas y pocas ganancias enormes es típico de un seguidor de tendencia; ganancias chicas y alguna pérdida enorme es típico de reversión a la media (y peligroso).

### 6.4 Risk

Todas las métricas agrupadas: **Risk** (drawdown, Sharpe, Sortino, Calmar, VaR, CVaR), **Performance**, **Trades**, **Exposure** y **Costos** (PnL bruto, comisiones, funding, slippage). Pasá el mouse por el ícono de cada métrica para ver la definición.

Los **costos** son clave: comparar el PnL bruto con las comisiones y el funding te muestra cuánto se "come" el mercado. Una estrategia con mucho PnL bruto y casi nada de neto vive de costos que no controlás.

Recordá que el drawdown se mide **al cierre de cada vela**, no dentro de ella: el drawdown intravela real podría ser peor.

### 6.5 Monte Carlo

**Para qué sirve.** Tu backtest es *un solo camino* posible. Con las mismas operaciones, en otro orden o con otra suerte, el recorrido habría sido distinto. Monte Carlo remuestrea los retornos de las operaciones miles de veces para estimar **cuánto depende el resultado de la suerte**. Se corre con el botón **Correr simulación** y usa las opciones de la sección D.

**Los tres métodos:**

| Método | Qué hace | Cuándo usarlo |
|---|---|---|
| **Shuffle** | Reordena las mismas operaciones. El retorno final **no cambia**; solo varía el recorrido (el drawdown). | Para saber si el drawdown observado fue suerte o mala suerte. |
| **Bootstrap** | Sortea operaciones **con reposición**. Varía también el retorno final. Supone operaciones independientes. | Para ver el rango posible de retornos. |
| **Block bootstrap** | Como bootstrap pero copia **bloques** de operaciones consecutivas, conservando las rachas. | Si las operaciones muestran dependencia (rachas de ganancias o pérdidas). |

**Qué muestra:**
- **Retorno final** (P5, P25, mediana, P75, P95). *P5 es el caso pesimista*: en solo el 5% de las simulaciones fue peor.
- **Drawdown máximo:** P5 (peor caso), mediana y P95 (mejor caso).
- **Probabilidad de pérdida:** en qué % de las simulaciones se terminó perdiendo.
- **Probabilidad de drawdown mayor al umbral** (el de la sección D).
- **Resultado real:** tu retorno y drawdown reales, y *en qué % de las simulaciones el drawdown real fue peor*. Si tu drawdown real es mucho **mejor** que la mediana simulada, tuviste suerte con el orden de las operaciones: esperá peor.
- Histogramas con las marcas P5, mediana y real.

**Cómo leerlo:** si la mediana de retorno es positiva pero el P5 es negativo y la probabilidad de pérdida es alta, el resultado es frágil. Los avisos de la pestaña indican cuando hay pocas operaciones o dependencia serial.

**Límite:** el drawdown de Monte Carlo se mide sobre operaciones cerradas: no captura la caída *dentro* de una operación. Y supone que el futuro se parece al pasado.

### 6.6 Robustness (robustez)

Acá se responde: *¿el resultado se sostiene si toco los parámetros?*

**Sensibilidad a parámetros.** Elegí **uno o dos parámetros**, con un mínimo, un máximo y un paso (hasta 300 combinaciones). El sistema corre la estrategia en cada combinación.
- Con **un parámetro**, muestra una línea por métrica (retorno, Sharpe, Sortino, drawdown, profit factor y expectativa) con una línea punteada en el valor actual.
- Con **dos**, muestra un **mapa de calor**: **azul = bien, rojo = mal, gris = neutro** (0, o 1 para el profit factor). El recuadro negro es el punto actual. Podés cambiar la métrica que se colorea.
- Debajo, la tabla de **todas las combinaciones, sin ranking**. El sistema **nunca elige "la mejor"** por vos: no querés el pico, querés una zona estable.

**Cómo leerlo:** buscá **bloques amplios y contiguos** de colores parecidos. Si tu punto está en un pico aislado rodeado de rojo, es sobreajuste: un pequeño cambio destruye el resultado. Si los vecinos rinden parecido, la estrategia es robusta a ese parámetro. El sistema avisa cuando el punto actual es mucho mejor que todos sus vecinos, y cuando más de la mitad de las combinaciones tienen menos de 30 operaciones.

Hay además un **barrido multi-parámetro (avanzado)** para explorar varios parámetros a la vez en una tabla.

### 6.7 Validation (validación)

**Fuera de muestra (in-sample / out-of-sample).** Se corta el período en dos: el **in-sample** (por defecto el 70% inicial) y el **out-of-sample** (el resto). Podés cortar por porcentaje o por una **fecha**. Se aplican los **mismos parámetros** a ambos tramos y se muestran lado a lado. El tramo out-of-sample **arranca de cero** con el capital inicial y usa la historia anterior solo para calentar los indicadores.
- **Cómo leerlo:** si gana en el in-sample y pierde en el out-of-sample, es la señal clásica de sobreajuste. También avisa si el Sharpe fuera de muestra es menos de la mitad del de adentro.
- **Regla de oro:** si ajustaste los parámetros mirando el out-of-sample, **ese tramo ya no es fuera de muestra**. Elegí los parámetros con el in-sample y usá el out-of-sample **una sola vez** para confirmar.

**Walk-forward.** Desliza una ventana de **entrenamiento** seguida de una de **test** a lo largo de toda la historia (por defecto 45, 15 y 15 días de entrenamiento, test y paso) y mide **cada test por separado**.
- **Parámetros fijos:** evalúa los mismos parámetros en todas las ventanas de test.
- **Re-optimizar en cada ventana:** en cada ventana elige los mejores parámetros usando **solo el entrenamiento** y los evalúa **una única vez** en el test siguiente. Elegís el objetivo (Sharpe, Sortino, retorno, profit factor o expectativa) y un mínimo de operaciones en el entrenamiento. Cuidado con optimizar Sharpe cuando hay pocas operaciones.
- **Qué muestra:** el retorno de cada ventana (entrenamiento frente a test), la **curva fuera de muestra unida** (los tests consecutivos encadenados), un resumen (cuántas ventanas fueron ganadoras, retorno mediano) y el detalle por ventana.
- **Cómo leerlo:** un test muy peor que su entrenamiento sugiere sobreajuste. Si la mayoría de las ventanas pierde, la estrategia no se sostiene en el tiempo. Si los parámetros elegidos cambian mucho entre ventanas, la estrategia es inestable frente a ellos.
- Se avisa cuando la mayoría de las ventanas tienen menos de 10 operaciones, o cuando las ventanas se solapan.

### 6.8 Stress Test

Responde: *¿cuánto de la ganancia sobrevive cuando la realidad es peor que el modelo?* Tiene dos análisis. En ambos, **cada punto es una simulación completa** con el mismo motor y la misma estrategia (no un ajuste aproximado sobre el resultado), así que las cifras son directamente comparables con tu backtest. Podés cancelarlos mientras corren.

**Sensibilidad a costos.** Varía un costo por vez, y en cruce, sobre tu configuración:
- **Slippage:** 0, 1, 2, 5, 10, 20 y 50 bps.
- **Comisiones:** de ×0 a ×3 las que configuraste (taker y maker).
- **Funding:** sin funding, constante (0,01% y 0,03% cada 8 horas), histórico real y *histórico siempre en contra*.
- **Cruce slippage × comisiones:** un mapa de calor del retorno total.

Y estima el **punto de equilibrio**: el slippage (y cuántas veces las comisiones) con el que el retorno pasa de positivo a negativo, por interpolación entre los puntos probados. Si no se anula dentro del rango, lo dice.

**Cómo leerlo.** Un slippage de equilibrio de 15 bps en BTCUSDT indica que la estrategia tolera bastante fricción; uno de 2 bps es muy frágil, porque en operaciones reales el slippage en momentos de volatilidad puede llegar a esa cifra o más. Si el color pasa de azul a rojo ya en la primera fila o columna del mapa de calor, la ganancia depende de costos casi nulos.

**Stress tests.** Diez escenarios fijos, en este orden y **sin ranking**:

| Escenario | Qué cambia |
|---|---|
| Escenario base | Tu configuración tal cual. |
| Comisiones ×2 | Las comisiones taker y maker se duplican. |
| Slippage ×2 y ×3 | Se multiplica el slippage configurado. Si es 0, se parte de una referencia de 5 bps (entonces 10 y 15). |
| Funding adverso | Se usa el funding real, **siempre como costo** para la posición, sea larga o corta. Sin funding histórico disponible, un 0,03% cada 8 horas. |
| Volatilidad ×1,5 y ×2 | **Mercado sintético** con cada retorno de vela 1,5 o 2 veces mayor. La estrategia decide sobre esos precios. |
| Stops con gap de 1% y 3% | Cada stop-loss se ejecuta 1% o 3% peor que su nivel (saltos de precio en eventos de mercado). |
| Peor caso combinado | Comisiones ×2, slippage ×3, funding adverso y stops con gap de 1%, todo junto. |

La tabla muestra retorno, **diferencia contra el base (en puntos porcentuales)**, drawdown, Sharpe, profit factor, capital final y operaciones, con una marca *Gana* o *Pierde*. Debajo, un gráfico de barras del retorno por escenario. Pasá el mouse por el nombre para ver qué cambia en cada uno.

**Cómo leerlo.**
- Buscá cuántos escenarios terminan en pérdida y **qué tan grande es la caída** en el peor caso combinado. Una estrategia que pasa de +14% a −19% en el peor caso es frágil aunque el base luzca bien.
- Los stops con gap suelen ser lo más dañino para las estrategias con stops ajustados.
- La **volatilidad ×2** usa precios fabricados: es una prueba de sensibilidad, no una predicción. Que el resultado mejore (puede pasar con seguidores de tendencia) no significa que sea una buena señal.
- En los mercados sintéticos no existen las velas de menor timeframe para resolver stop y take-profit: en esos dos escenarios se asume el stop primero.
- Si el escenario base ya pierde, el estrés solo confirma que empeora.

Ambos análisis se pueden descargar (Excel o CSV).

### 6.9 History (historial)

Cada corrida se **guarda sola** con todo lo necesario para volver a verla: la configuración completa (ejecución, riesgo, validación y semilla), la versión de la estrategia (con una huella del código), el capital, las operaciones y la curva de capital. La tabla muestra fecha, estrategia y versión, mercado, período, un resumen de costos, retorno, Sharpe, drawdown, operaciones y si tiene los datos completos. Se ordena por la mayoría de las columnas.

Elegí filas con la casilla de la izquierda:

| Acción | Qué hace | Cuántas filas |
|---|---|---|
| **Ver** | Muestra la corrida en las pestañas de resultados (Overview, Equity, Trades, Risk, Monte Carlo…), como si la acabaras de correr. | 1 |
| **Clonar configuración** | Carga en el panel de arriba la estrategia, los parámetros y todos los ajustes de esa corrida, para repetirla o probar una variante. | 1 |
| **Comparar** | Arma la comparación debajo de la tabla (ver abajo). | 2 a 6 |
| **Descargar corrida** | Excel con Métricas, Avisos, Configuración, Operaciones y Equity. | 1 |
| **Eliminar** | Borra las corridas elegidas, con confirmación. | 1 o más |
| **Descargar listado** | Todas las corridas en una tabla. | — |

**La comparación** muestra, en el orden en que elegiste las corridas (**no hay ranking automático**):
1. **Qué cambió entre las corridas:** solo los ajustes que difieren (parámetros, costos, funding, semilla…). Lo que es igual en todas no aparece. Así ves de un vistazo por qué dos resultados son distintos.
2. **Métricas lado a lado:** todas las métricas de cada corrida.
3. **Curvas de capital:** el retorno acumulado (%) de cada una en una sola escala. El color depende de la posición en la selección, no del resultado.

Un aviso te alerta si las corridas usan **símbolos, timeframes o períodos distintos**: en ese caso la comparación directa engaña, porque otro mercado o otro tramo de historia puede explicar la diferencia por sí solo.

**Corridas antiguas.** Las guardadas antes de esta función solo tienen parámetros y métricas y figuran con *Datos completos: No*. No se pueden ver ni descargar en detalle, pero sí clonar (los costos y el riesgo se cargan con valores por defecto) y comparar sus métricas.

**Tamaño.** La curva de capital de una corrida muy larga se guarda con menos puntos (como máximo unos 4.000) para no llenar la base: alcanza para verla y compararla, y las métricas de riesgo se guardan ya calculadas.

---

## 7. Cómo interpretar las métricas

Los rangos son **orientativos**, no reglas: dependen de la estrategia, el mercado y la muestra.

| Métrica | Qué responde | Orientación | Trampa frecuente |
|---|---|---|---|
| **Retorno total** | ¿Cuánto ganó? | — | Sin mirar el riesgo ni los costos no significa nada. |
| **CAGR** | Retorno anualizado | Solo válido con 1 año o más | Con períodos cortos extrapola y engaña. |
| **Drawdown máximo** | ¿Cuánto llegó a caer? | Preguntate si aguantarías esa caída | Se mide al cierre de vela: el real puede ser peor. |
| **Sharpe** | Retorno por unidad de riesgo | Más de 1 es aceptable, más de 2 es muy bueno | Con pocas operaciones no significa nada. |
| **Sortino** | Como Sharpe, pero solo cuenta la volatilidad a la baja | Similar al Sharpe | Igual que el Sharpe. |
| **Calmar** | CAGR ÷ drawdown máximo | Cuanto mayor, mejor | Depende del CAGR: no usarlo con menos de 1 año. |
| **Profit factor** | Ganancias ÷ pérdidas | Mayor a 1 = gana más de lo que pierde | Valores muy altos con pocas operaciones suelen ser suerte. Sin pérdidas, es infinito (no definido). |
| **Win rate** | % de operaciones ganadoras | No dice nada solo | Un 35% puede ser excelente (seguidor de tendencia) y un 80% pésimo (pocas ganancias enormes y pérdidas gigantes). Mirala junto al tamaño de ganancias y pérdidas. |
| **Expectativa** | Ganancia promedio por operación | Debe ser claramente mayor que los costos | Depende de pocas operaciones grandes. |
| **VaR / CVaR 95%** | Pérdida típica y promedio en las peores velas | — | Es por vela, no por operación. |
| **Tiempo en mercado** | % de velas con posición | — | Un tiempo bajo con buen retorno puede ser suerte. |

**Tamaño de muestra.** Menos de **10** operaciones: no concluyas nada. Entre **10 y 29**: alta incertidumbre. Desde **30**: recién razonable. Cuantas más, mejor.

### Lista de verificación antes de confiar en una estrategia

1. ¿Probé al menos **1 año** de historia y al menos **30 operaciones**?
2. ¿Usé **costos realistas** (comisión, slippage, spread y funding histórico)?
3. ¿El modelo de ejecución es **apertura de la vela siguiente**?
4. ¿Leí **todos los avisos** de la pestaña Overview?
5. ¿La **ganancia depende de una o dos operaciones enormes**? (mirá la mejor operación frente al total).
6. ¿El resultado se mantiene si cambio un poco los parámetros (**mapa de calor con una zona amplia**, no un pico)?
7. ¿Aguanta **fuera de muestra**?
8. ¿La mayoría de las ventanas de **walk-forward** son ganadoras?
9. ¿**Monte Carlo** muestra una probabilidad de pérdida y un drawdown que puedo tolerar?
10. ¿Da resultados parecidos en **otros timeframes, otros períodos y otros símbolos**?

Si falla varias, **descartala**: es lo que el backtester está para hacer.

### Señales de sobreajuste

- Ganancia excelente en un tramo y mala en el otro.
- Un pico aislado en el mapa de calor.
- El signo del resultado cambia al pasar de 1 hora a 4 horas.
- Los parámetros óptimos cambian mucho entre ventanas de walk-forward.
- El resultado depende de muy pocas operaciones.
- Cuantos más parámetros ajustaste, más sospechoso el resultado.

---

## 8. Ejemplo comentado

Corrida real: **Donchian, BTCUSDT, 4 horas, 365 días**, parámetros por defecto, comisión de 0,055% y sin slippage, spread ni funding.

| Métrica | Valor |
|---|---|
| Operaciones | 44 |
| Retorno total | +11,5% |
| Drawdown máximo | −9,5% |
| Profit factor | 1,48 |
| Win rate | 34,1% |
| Sharpe | 1,03 |

**Lectura paso a paso:**
1. **Muestra:** 44 operaciones supera el mínimo de 30, así que es razonable (aunque no abundante). Sin aviso de muestra insuficiente.
2. **Retorno contra riesgo:** +11,5% con −9,5% de drawdown: ganó poco más de lo que llegó a caer. Sharpe ≈ 1: aceptable, sin ser excepcional.
3. **Win rate de 34% con profit factor de 1,48:** implica que la ganancia promedio es cerca de **2,9 veces** la pérdida promedio. Es el perfil típico de un seguidor de tendencia: pierde seguido, pero poco; gana de vez en cuando, mucho.
4. **Costos optimistas:** no se incluyeron slippage, spread ni funding. Con costos realistas el resultado **bajará**. Hay que rehacerlo y ver cuánto sobrevive.
5. **Sin validación:** es un resultado *en muestra*: los parámetros no se ajustaron, pero esto es un solo año, un solo activo y un solo camino.

**Qué haría a continuación:** repetir con slippage de 3 bps, spread de 1 bps y funding histórico; correr Monte Carlo (probabilidad de pérdida y drawdown en el P5); mirar la sensibilidad a `entry_bars` (¿20 es un pico o hay una zona amplia?); probar fuera de muestra y walk-forward sobre 2 años; y repetirlo en ETHUSDT. Solo si sobrevive a todo eso, probarla en Demo.

---

## 9. Limitaciones conocidas

Conocelas para no sobreinterpretar:

- **Una posición por vez.** No simula acumular ni promediar posiciones.
- **Sin liquidación ni margen.** No modela que Bybit liquide una posición apalancada si el precio va muy en contra. El apalancamiento se trata como un tope de exposición.
- **Drawdown al cierre de vela.** Las caídas dentro de una vela no se miden.
- **Sin impacto de mercado ni latencia.** El slippage es un valor fijo que vos elegís; en momentos de mucha volatilidad o con tamaños grandes puede ser mucho peor.
- **Órdenes límite.** Se asume que se ejecutan si el precio las alcanza; en la realidad puede haber cola y quedar sin ejecutar.
- **Un solo exchange y un solo símbolo por corrida.** El sesgo de supervivencia depende del símbolo que elijas (elegir uno que hoy es popular puede favorecer los resultados).
- **El pasado no es el futuro.** El mercado cambia. Y Monte Carlo asume que el futuro se parece al pasado.
- **Funding y open interest** (para la estrategia que los usa): a cada vela se le asigna el último dato conocido antes de que abriera. Es conservador, pero puede tener una vela de retraso.
- **Los stress tests son escenarios, no predicciones.** Los mercados con más volatilidad son sintéticos (retornos escalados sobre los precios reales) y el gap de los stops es un valor fijo que vos no elegís. Sirven para medir fragilidad, no para estimar qué va a pasar.
- **La comparación no rankea.** Comparar muchas variantes mirando cuál dio mejor resultado es una forma de sobreajuste: la "ganadora" de veinte probadas suele ser la más afortunada.

---

## 10. Reproducibilidad y descarga

- Cada corrida guarda su **configuración completa**, la **versión de la estrategia** (con una huella del código), la **semilla** de Monte Carlo, las operaciones y la curva de capital, para poder repetirla exactamente. Con **Clonar configuración** la cargás en el panel con un clic. Si cambia la lógica de la estrategia, cambia la huella.
- **Descargar resultados** (Excel o CSV) exporta, en hojas separadas: **Métricas**, **Avisos**, **Configuración**, **Operaciones** (una fila por trade, con todos los costos) y **Equity** (curva y drawdown). Las cifras salen con toda su precisión.
- Pasos para reproducir un resultado: mismos símbolo, timeframe, período, parámetros, costos y semilla.
