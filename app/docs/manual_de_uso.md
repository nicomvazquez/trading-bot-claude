# Manual de uso

Este manual explica cómo funciona el sistema y cómo usarlo, pantalla por pantalla. Las estrategias están explicadas en su propia guía y el backtester en la suya.

> **Aviso.** Este software es una herramienta de investigación y automatización. No es asesoramiento financiero. Operar futuros con apalancamiento puede hacerte perder todo el capital. Los resultados históricos no garantizan resultados futuros. Usá Demo Trading hasta tener evidencia sólida, y no arriesgues dinero que no podés perder.

## 1. Qué es el sistema

Un bot de trading para **futuros perpetuos de Bybit** con cuatro partes:

| Parte | Para qué sirve |
|---|---|
| **Estrategias** | Reglas que deciden cuándo comprar o vender. Cada una se configura y se ejecuta como una *instancia*. |
| **Operativa en vivo** | Ejecuta las instancias activas sobre la cuenta de Bybit (hoy en **Demo Trading**, con fondos virtuales). |
| **Backtester** | Prueba una estrategia sobre datos históricos reales antes de arriesgar nada, y mide qué tan confiable es el resultado. |
| **Dashboard** | Esta interfaz web: ver cómo rinde el bot, controlarlo y descargar los datos. |

Todo corre en un solo programa (Python) con una base de datos PostgreSQL, dentro de Docker.

## 2. Cómo se arranca y se abre

Desde la carpeta del proyecto:

```bash
docker compose up -d          # arranca la base de datos, Redis y la app
```

Después abrí **http://localhost:8080** (o `http://IP-DEL-SERVIDOR:8080` desde otro equipo de tu red).

| Comando | Qué hace |
|---|---|
| `docker compose up -d` | Arranca todo. Necesario después de cambiar el archivo `.env`. |
| `docker compose restart app` | Reinicia solo la app (por ejemplo, después de actualizar código). |
| `docker compose logs -f app` | Muestra en vivo lo que hace la app, para diagnosticar. |
| `docker compose stop` | Apaga todo (las posiciones abiertas siguen en Bybit con su stop-loss). |
| `docker compose --profile demo up -d app-demo` | Levanta la **copia de demostración** (ver sección 7). |

> **Importante:** el dashboard **no tiene login**. Está pensado para un servidor privado. No lo expongas a internet. Ver sección 9.

### Configuración inicial (una sola vez)

1. Copiá `.env.example` a `.env`.
2. Creá una API key de **Demo Trading** en bybit.com (perfil → API → Create New Key), activando el interruptor *Demo Trading* antes de generarla. **Sin permiso de retiro.** La clave de demo es distinta de la de mainnet.
3. Completá en `.env`: `BYBIT_API_KEY`, `BYBIT_API_SECRET` y una `DB_PASSWORD` larga y única. Dejá `BYBIT_ENV=demo`.
4. `docker compose up -d` y abrí el dashboard. En **Configuración → Probar conexión** tiene que decir *Conexión correcta*.
5. En Configuración podés pulsar **Pedir fondos demo** para cargar saldo virtual.

## 3. El mapa de pantallas

El menú de la izquierda tiene cinco pantallas. Abajo, una caja muestra el entorno (**Demo Trading** o **MAINNET**), cuántas instancias corren y una advertencia si el kill-switch está activo. En pantallas chicas, el menú se abre con el botón ☰.

### Horarios

Todas las horas que muestra el sistema (operaciones, actividad, gráficos, períodos del backtest, exportaciones y el reloj del menú lateral) están en **hora argentina (UTC-3, sin horario de verano)**. Internamente todo se guarda en UTC y se convierte al mostrarlo. El «día» del PnL de hoy y del límite de pérdida diaria empieza a las **00:00 de Argentina**. Si necesitás otra zona, cambiá `APP_TIMEZONE` en `.env` (por ejemplo `America/Montevideo`) y reiniciá.

Las **kill zones** de la estrategia ICT se calculan siempre en hora de Nueva York, sin importar la zona configurada.

### 3.1 Resumen

Es el tablero: cómo está la cuenta y las estrategias ahora. Se actualiza solo cada 15 segundos.

- **Franja de alertas** (arriba, solo aparece si hace falta): kill-switch activado, no se pudo leer el balance, no hay instancias corriendo, no hay instancias creadas, o alguna instancia tiene su último evento como señal rechazada o error. Cada alerta trae un enlace para resolverla.
- **Balance de margen:** lo que respalda tus posiciones (USDT y USDC). Debajo, el *Disponible*. Pasá el mouse por el ícono ℹ para ver el *equity total*, que suma además otras monedas de la cuenta (en Demo, Bybit regala 1 BTC y 1 ETH que **no** cuentan como margen). Por eso el equity total es bastante mayor que el balance de margen.
- **PnL realizado:** suma de las ganancias y pérdidas de los trades **cerrados por el bot**, según lo que informa Bybit (ya con comisiones).
- **PnL de hoy:** lo mismo pero solo de los trades cerrados desde las 00:00 de hoy, **hora argentina**.
- **Posiciones abiertas** y su PnL no realizado.
- **Win rate, Profit factor e Instancias** (corriendo / totales).
- **Capital realizado:** una curva con el capital inicial de las instancias activas más el PnL de los trades cerrados. No incluye la posición abierta, así que solo se mueve al cerrar operaciones.
- **Instancias** y **Posiciones abiertas**: tablas con el estado de cada una (Corriendo, Sin runner, Apagada), su capital, PnL y lado de la posición (▲ Long / ▼ Short).
- **Descargar** (arriba a la derecha): Excel con las hojas *Instancias*, *Trades* y *Órdenes*, o CSV de cada una.

### 3.2 Operaciones

El historial de todo lo que hizo el bot. Se actualiza cada 20 segundos.

- **Filtros:** instancia, estado (abiertas o cerradas) y lado (long o short). Debajo se ve el resumen de lo filtrado: cantidad de trades, PnL, win rate y profit factor.
- **Trades:** cada fila es una posición completa. Columnas: apertura, instancia, símbolo, lado, cantidad, precios de entrada y salida, stop, take profit, PnL en USD, motivo de cierre y fecha de cierre. Se puede ordenar por apertura o PnL.
- **Motivos de cierre:** *Stop loss*, *Take profit*, *Señal* (la estrategia mandó cerrar), *Flip de posición* (cerró para abrir la contraria) y *Stop/TP del exchange* (Bybit cerró la posición por sí solo con el stop-loss o take-profit que dejó puesto el bot).
- **Órdenes enviadas:** las últimas 200 órdenes que el bot mandó al exchange, con su ID en Bybit.
- **Descargar:** hay un botón en cada tarjeta. El de Trades **respeta los filtros** que tengas puestos.

### 3.3 Estrategias

Acá se crean y controlan las instancias. Una **instancia** es una estrategia ya configurada: con su símbolo, timeframe, capital y parámetros. Podés tener varias instancias de la misma estrategia.

**Catálogo.** Arriba están las estrategias disponibles, con una descripción. El botón *Crear instancia* abre el formulario.

**Formulario de instancia:**

| Campo | Qué es |
|---|---|
| Nombre | Único. Es como la vas a ver en todo el sistema. |
| Símbolo | El par de Bybit, por ejemplo `BTCUSDT`. Se valida contra Bybit. |
| Timeframe | Cada cuánto cierra una vela: 1, 5, 15 minutos, 1 hora, 4 horas o 1 día. |
| Capital asignado (USD) | **Capital virtual** de esta instancia. No reserva fondos en Bybit: solo se usa para calcular el tamaño de cada operación. |
| Parámetros | Los de la estrategia. Al pasar el mouse por cada etiqueta de la tarjeta ves su descripción. |
| Chequeo de tamaño | Simula cuánto abriría con esos datos y avisa si quedaría por debajo del mínimo del símbolo (ver 4.4). |

**Tarjeta de cada instancia:** estado, interruptor *Activa*, capital, PnL realizado (en USD y %), PnL de hoy, cantidad de trades, win rate, profit factor, posición abierta, última actividad y los parámetros como etiquetas.

**Acciones:**

- **Activa** (interruptor): enciende o apaga la instancia. Al encender, **espera la próxima vela cerrada** antes de operar (ver 4.2).
- **Editar:** cambia cualquier valor. Si está corriendo, se reinicia con los nuevos valores y **conserva la posición abierta**.
- **Duplicar:** crea una copia apagada, para probar variantes.
- **Eliminar:** solo si está apagada y no tiene trades. Si tiene historial, no se elimina, para no perder ese registro.
- **Actividad reciente:** una bitácora desplegable con lo que hizo la instancia: encendida, apagada, abierta, cerrada, **señal rechazada** (con el motivo) o error. Es lo primero que hay que mirar cuando una instancia "no opera".

### 3.4 Backtesting

El laboratorio para probar estrategias con datos históricos. Tiene su propia guía completa: **Guía del backtester**.

### 3.5 Configuración

- **Kill-switch:** interruptor de emergencia. Al activarlo, **ninguna estrategia abre operaciones nuevas**, y se aplica al instante. **No cierra** las posiciones que ya están abiertas: esas conservan su stop-loss y take-profit en Bybit, y las señales de cierre de las estrategias se siguen ejecutando.
- **Conexión con Bybit:** entorno (Demo o MAINNET), si la API key está configurada, *Probar conexión* y *Pedir fondos demo* (solo en Demo).
- **Límites de riesgo** (se aplican a todas las instancias; ninguna estrategia puede saltearlos):
  - *Pérdida diaria máxima por instancia (%):* si una instancia pierde más que esto en el día (de 00:00 a 24:00, hora argentina), deja de abrir operaciones hasta el día siguiente.
  - *Posiciones simultáneas máximas:* tope global, sumando todas las instancias.
  - *Apalancamiento máximo (x):* tope de exposición: el valor nocional de una posición no supera el capital × este número. **No define el tamaño**: eso sale del riesgo por operación.

## 4. Cómo opera el bot en vivo

### 4.1 El ciclo de una operación

Cada instancia activa corre en segundo plano y cada ~20 segundos:

1. **Sincroniza con Bybit:** si el exchange cerró la posición solo (por el stop-loss o take-profit), lo registra con el PnL real. Si hay una posición en el exchange sin registro y ninguna otra instancia la reclama, la adopta.
2. **Espera una vela nueva cerrada.** Las estrategias deciden **solo con velas ya cerradas**, nunca con la que se está formando.
3. **Le pregunta a la estrategia** si hay señal (comprar, vender o cerrar).
4. **Pasa la señal por el gestor de riesgo:** kill-switch, pérdida diaria, posiciones simultáneas y tamaño (ver 4.3). Si algo no se cumple, la señal se descarta y **queda anotada con su motivo** en la actividad.
5. **Envía una orden de mercado** con el stop-loss y el take-profit ya puestos en Bybit. Si el bot se cae, esas protecciones siguen vigentes en el exchange.
6. **Registra** la orden y el trade. Al cerrarse, guarda el PnL informado por Bybit.

### 4.2 Reglas que conviene conocer

- **Una sola instancia activa por símbolo.** Bybit tiene una única posición por símbolo y cuenta: dos instancias operando `BTCUSDT` se pisarían. El sistema no te deja encender una segunda. Para correr varias estrategias a la vez, usá símbolos distintos (una en `BTCUSDT`, otra en `ETHUSDT`).
- **Al encender, espera la próxima vela.** No opera con una señal anterior al encendido. Con timeframe de 15 minutos, puede pasar hasta ese tiempo sin operaciones: es normal. Queda anotado en la actividad.
- **Reinicios.** Al reiniciar la app, las instancias activas arrancan solas, retoman su posición abierta y también esperan la próxima vela.
- **Decisión al cierre de la vela.** Si una estrategia es lenta (4 horas), puede pasar mucho tiempo sin actividad. Eso no es un fallo.

### 4.3 Cómo se calcula el tamaño

El sistema usa **riesgo fijo por operación**:

```
cantidad = capital de la instancia × riesgo% ÷ distancia al stop
```

Ejemplo: capital 1.000 USD, riesgo 1% (= 10 USD), precio 84.000 y stop 3% (= 2.520 USD de distancia): cantidad = 10 ÷ 2.520 = 0,00397 BTC, que se redondea hacia abajo al paso del símbolo (0,001) → **0,003 BTC**.

- El **capital de la instancia** es su capital asignado más el PnL que ya realizó.
- El tamaño se topa por el **apalancamiento máximo** × capital.
- Se redondea al paso del símbolo. Si queda por debajo del **mínimo del símbolo** (0,001 BTC en BTCUSDT), la señal se descarta con un aviso.

### 4.4 Por qué una instancia puede no operar (y cómo verlo)

Abrí la **Actividad reciente** de la instancia. Las causas típicas:

| Mensaje | Qué significa | Qué hacer |
|---|---|---|
| *Esperando la próxima vela cerrada* | Acaba de encenderse. | Esperar. |
| *Señal descartada: el tamaño calculado es menor al mínimo* | El capital es demasiado chico para el riesgo y stop elegidos. | Subir el capital o el % de riesgo. El *Chequeo de tamaño* del formulario lo anticipa y sugiere el capital mínimo. |
| *Señal rechazada por riesgo: kill-switch activado* | Está el kill-switch. | Desactivarlo en Configuración. |
| *Señal rechazada por riesgo: pérdida diaria…* | La instancia superó su límite diario. | Se libera al día siguiente (a las 00:00, hora argentina), o subir el límite. |
| *Señal rechazada por riesgo: ya hay N posiciones abiertas* | Se alcanzó el tope global. | Cerrar alguna o subir el tope. |
| *Error en el ciclo…* | Falló una llamada a Bybit u otro problema. | Ver el mensaje. Los errores transitorios se reintentan solos. |
| *(sin mensajes de rechazo)* | La estrategia simplemente no encontró señal. | Es lo normal la mayor parte del tiempo. |

## 5. Flujo recomendado: de la idea a operar

1. **Elegí una estrategia** y entendela (Guía de estrategias).
2. **Backtesteala** sobre al menos 1 año, con costos realistas (comisión, slippage, funding histórico).
3. **Validala:** fuera de muestra, walk-forward y sensibilidad a parámetros (Guía del backtester). Descartá sin miedo lo que no aguanta.
4. **Creá una instancia** con capital chico y riesgo bajo, y confirmá con el *Chequeo de tamaño* que puede operar.
5. **Encendela en Demo** y dejala semanas. Mirá la actividad y las operaciones.
6. **Compará lo real contra el backtest.** Si difieren mucho, algo del modelo no coincide con la realidad.
7. Recién con evidencia sostenida, y sabiendo lo que hacés, pensá en mainnet (sección 9).

## 6. Descargar los datos (Excel y CSV)

El botón **Descargar** aparece en Resumen, Operaciones y Backtesting (resultados, corridas guardadas, sensibilidad a costos y stress tests).

- **Excel (.xlsx):** el recomendable para abrir en Excel. Trae todas las hojas, encabezado, filtros y fechas reales.
- **CSV:** una sola tabla, UTF-8 con acentos, separado por comas y con punto decimal. Sirve para pandas u otras herramientas. Si tu Excel está en configuración regional en español, un CSV abierto con doble clic puede mostrarse en una sola columna (allí el separador esperado es `;`): usá el `.xlsx`.
- Las fechas van en **hora argentina** (el título de la columna lo dice) y los números con toda su precisión, sin el redondeo de pantalla.

## 7. Modo demostración (datos de ejemplo)

Para ver todos los paneles con contenido sin tocar la base real ni operar:

```bash
docker compose --profile demo up -d app-demo      # abre http://localhost:8081
docker compose --profile demo stop app-demo       # la apaga
```

Es una copia del dashboard con datos **sintéticos** en una base aparte (`trading_demo`). Muestra una franja violeta *MODO DEMOSTRACIÓN*, **no puede encender instancias ni enviar órdenes**, y sus resultados no son reales. La primera vez hay que crear y sembrar la base; los comandos están en el README.

## 8. Mantenimiento

- **Ver qué pasa:** `docker compose logs -f app`.
- **Backup de la base** (recomendado, por ejemplo una vez por semana):

  ```bash
  docker compose exec -T db pg_dump -U <DB_USER> <DB_NAME> > backup_$(date +%F).sql
  ```

  Restaurar: `docker compose exec -T db psql -U <DB_USER> <DB_NAME> < backup_AAAA-MM-DD.sql`
- **Cambiar la contraseña de la base ya creada:** además de editar `.env`, hay que ejecutar dentro de Postgres `ALTER USER <usuario> WITH PASSWORD '<nueva>';` y luego `docker compose up -d`.
- **Actualizar el código:** la carpeta `app/` está montada dentro del contenedor; alcanza con `docker compose restart app`. Si cambian las dependencias, `docker compose up -d --build`.
- **Datos históricos del backtester:** se guardan en la base como caché; descargarlos la primera vez tarda, después es rápido.

## 9. Seguridad

- **Sin login.** El dashboard permite encender y apagar el bot, cambiar límites y ver posiciones. Cualquiera que llegue al puerto lo controla. Usalo en un servidor privado.
- Postgres (5432) y Redis (6379) solo escuchan en `127.0.0.1`.
- El dashboard escucha en `APP_BIND` (`0.0.0.0` = toda la red). Si el servidor tiene salida a internet, poné `APP_BIND=127.0.0.1` y accedé por túnel SSH o VPN, o cerrá el puerto en el firewall.
- Las claves de Bybit viven solo en `.env` (excluido de git). Creá la API key **sin permiso de retiro** y, si podés, restringida a la IP de tu servidor.
- La app avisa si `DB_PASSWORD` es la de por defecto y, en MAINNET, se niega a arrancar.

### Pasar a mainnet

Hoy el entorno se elige con `BYBIT_ENV` en `.env` (`demo` o `mainnet`), y todavía **no hay un selector en el dashboard**. Con `mainnet`, aparece una franja roja permanente. Antes de considerarlo: semanas de operativa en Demo con resultados coherentes con el backtest, capital chico que puedas perder por completo, la API key de mainnet sin permiso de retiro, y el kill-switch probado.

## 10. Solución de problemas

| Síntoma | Causa probable y solución |
|---|---|
| `invalid request, please check your server timestamp` en los logs | El reloj de la máquina o de Docker está desfasado respecto de Bybit. El sistema lo compensa solo; si persiste, sincronizá el reloj (en Windows con WSL: `wsl --shutdown` y reabrir Docker). |
| *Conexión: error 401 / clave inválida* | La clave de Demo Trading y la de mainnet son distintas. Verificá `BYBIT_ENV` y que la clave corresponda. Después de editar `.env`, usá `docker compose up -d` (un simple `restart` no relee el archivo). |
| Una instancia enciende pero no opera | Mirá su **Actividad reciente** (sección 4.4). |
| *«X» ya está operando BTCUSDT* | Una sola instancia activa por símbolo (sección 4.2). |
| El *equity total* es mucho mayor que el *balance de margen* | Demo incluye 1 BTC y 1 ETH que no cuentan como margen. Es normal. |
| La página muestra *Connection lost* | La app se reinició o se cayó. Recargá; si sigue, `docker compose logs app`. |
| El backtest no encuentra velas | Símbolo mal escrito o sin historia en ese rango. |
| Un backtest tarda mucho | Timeframes chicos (1 o 5 minutos) y muchos días generan muchísimas velas; ICT y el retroceso en 15 minutos son los más lentos. Probá menos días o un timeframe mayor. |
| Bybit rechaza la clave (mensaje de clave inválida o permisos) | La API key no tiene los permisos necesarios (lectura y trading de derivados), venció, o es de otro entorno (Demo vs mainnet). Creá una nueva. |

## 11. Glosario

- **Vela (kline):** resumen de precios de un período: apertura, máximo, mínimo, cierre y volumen.
- **Timeframe:** el período de cada vela (15 min, 1 hora, 4 horas…).
- **Perpetuo:** futuro sin vencimiento. Se mantiene cerca del precio real mediante el *funding*.
- **Funding:** pago periódico entre los que están largos y los que están cortos en un perpetuo (cada 8 horas: 00:00, 08:00 y 16:00 UTC, que son las 21:00, 05:00 y 13:00 en hora argentina). **No es una comisión de Bybit**: va de un lado del mercado al otro. Sirve para mantener el precio del perpetuo cerca del precio real: si el perpetuo cotiza por encima, el funding es positivo y **los largos le pagan a los cortos**; si cotiza por debajo, es negativo y **los cortos le pagan a los largos**. Suele rondar 0,01% cada 8 horas (unos 0,10 USD por pago sobre una posición de 1.000 USD), pero en tendencias muy fuertes puede subir bastante y volverse un costo importante para el lado sobrecargado.
- **Slippage de equilibrio:** el slippage con el que una estrategia deja de ganar. Cuanto mayor, más robusta es a la fricción real.
- **Stress test:** repetir una simulación bajo condiciones peores que las del modelo (costos duplicados, saltos de precio, más volatilidad) para medir qué tan frágil es el resultado.
- **Open interest (OI):** cantidad total de contratos abiertos en el mercado.
- **Long / Short:** apostar a que el precio sube / baja.
- **Stop-loss (SL):** orden que cierra la posición si el precio va en contra hasta un nivel. **Take-profit (TP):** cierra al alcanzar una ganancia objetivo.
- **Apalancamiento:** operar un valor mayor al capital. Amplifica ganancias y pérdidas. Acá es un **tope**, no define el tamaño.
- **Nocional:** valor total de la posición (cantidad × precio).
- **Riesgo por operación:** cuánto capital se pierde si la operación llega al stop.
- **R:** unidad de riesgo. Ganar 2R es ganar el doble de lo arriesgado.
- **Maker / Taker:** una orden límite que espera en el libro es *maker* (comisión menor); una orden de mercado que se ejecuta ya es *taker* (comisión mayor).
- **Slippage / Spread:** diferencia entre el precio esperado y el que realmente se obtiene.
- **ATR:** medida de la volatilidad: el rango promedio de las velas.
- **RSI:** indicador entre 0 y 100 que mide si el precio subió o bajó mucho y rápido (bajo = sobreventa, alto = sobrecompra).
- **SMA / EMA:** medias móviles del precio (simple y exponencial).
- **Drawdown:** caída desde un máximo de capital hasta el mínimo siguiente.
- **PnL:** ganancia o pérdida (*profit and loss*). **Realizado:** ya cerrado. **No realizado:** de una posición abierta.
- **Win rate:** % de operaciones ganadoras. **Profit factor:** ganancias totales ÷ pérdidas totales.
- **Sobreajuste (overfitting):** ajustar una estrategia tanto al pasado que solo funciona ahí.
- **Fuera de muestra (out-of-sample):** datos que no se usaron para diseñar o ajustar la estrategia.
- **Look-ahead:** usar sin querer información futura. El sistema está diseñado para evitarlo y tiene pruebas automáticas que lo verifican.
