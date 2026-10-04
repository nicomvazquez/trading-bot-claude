# Bot Trading - Bybit Futures

Bot de trading algorítmico para futuros perpetuos de Bybit, con estrategias
configurables en Python, un backtester de nivel de investigación y un
dashboard de operación en vivo. Todo en un solo proceso Python (FastAPI +
NiceGUI), sobre Postgres/TimescaleDB.

![Tests](https://github.com/nicomvazquez/trading-bot-claude/actions/workflows/tests.yml/badge.svg)

## Estado actual

- **Operativa en vivo** sobre Bybit Demo Trading o mainnet (elegible desde el
  dashboard, sin editar `.env` ni reiniciar): multi-instancia, una posición
  por símbolo, reconciliación automática contra el exchange, Risk Manager
  (kill-switch, pérdida diaria máxima por instancia y global, posiciones
  simultáneas máximas, apalancamiento máximo dinámico, mínimo de valor de
  orden en USD), botón de emergencia "Cerrar todo", alertas por Telegram,
  y registro de comisiones y funding reales por operación.
- **Backtester** de nivel de investigación: datos históricos reales de Bybit
  (cacheados en Postgres), modelo de ejecución configurable (fees, slippage,
  spread, funding, resolución intravela de stop/take-profit), sensibilidad a
  parámetros, barrido multi-parámetro, walk-forward (con o sin
  re-optimización por ventana), validación in-sample/out-of-sample, Monte
  Carlo (shuffle/bootstrap/block bootstrap) y stress tests. Todo exportable a
  Excel/CSV y con historial de corridas guardado en la base.
- **7 estrategias de ejemplo**: cruce de medias móviles, reversión RSI,
  ruptura de canal (Donchian), posicionamiento por funding + open interest,
  retroceso a favor de la tendencia (multi-timeframe), compresión/expansión
  de volatilidad (Bollinger) y una estrategia ICT (barrida de liquidez + Fair
  Value Gap). Interfaz `Strategy` común para todas, con registro automático
  y formularios de parámetros auto-generados en el dashboard.
- **Dashboard** con 6 páginas: Resumen, Operaciones, Estrategias,
  Backtesting, Configuración y Ayuda.

## Requisitos

- Docker Desktop (Postgres/TimescaleDB + la app corren en contenedores).
- Una API key/secret de **Demo Trading** de Bybit: se crea desde tu cuenta
  normal en bybit.com (perfil → API → Create New Key), activando el toggle
  "Demo Trading" antes de generarla. No es una cuenta aparte, pero la key
  es distinta de la de mainnet. Demo Trading corre sobre el mismo precio y
  la misma liquidez que producción, con fondos virtuales.
- Opcional: una API key/secret de **mainnet**, si además de probar en Demo
  vas a operar con dinero real. El dashboard permite elegir el entorno
  activo (Configuración → Conexión con Bybit) una vez que ambas credenciales
  están en `.env`; sin las de mainnet, ese cambio queda bloqueado.

## Cómo correrlo

```bash
cp .env.example .env
# completar BYBIT_API_KEY y BYBIT_API_SECRET en .env con las de Demo Trading

python -m app.tools.set_password           # o: docker compose exec app python -m app.tools.set_password
# pegar las dos líneas que imprime (APP_USER, APP_PASSWORD_HASH) en .env, y generar APP_STORAGE_SECRET
# con: python -c "import secrets; print(secrets.token_hex(32))"

docker compose up --build
```

Abrir http://localhost:8080. El dashboard exige login (usuario/contraseña fijos de `.env`); sin
`APP_PASSWORD_HASH`/`APP_STORAGE_SECRET` configurados, la app se niega a arrancar.

## Documentación

Los manuales están en `app/docs/` y también dentro de la aplicación, en la pantalla **Ayuda**:

- [Manual de uso](app/docs/manual_de_uso.md): cómo funciona el sistema y cada pantalla, cómo opera el bot en vivo, mantenimiento y solución de problemas.
- [Guía de estrategias](app/docs/estrategias.md): las siete estrategias, sus parámetros y cómo crear una propia.
- [Guía del backtester](app/docs/backtester.md): cómo configurarlo, cómo simula y cómo leer e interpretar cada resultado.

Un test (`tests/test_docs.py`) verifica que los parámetros, valores por defecto y rangos de la guía de estrategias
coincidan con el código.

## Validación de estrategias fuera de muestra

`scripts/validate_strategies.py` corre, para cada estrategia registrada, un backtest completo, un walk-forward con
parámetros fijos (ventanas rodantes no solapadas) y una simulación Monte Carlo, sobre ~2 años de historia. Sirve
como chequeo periódico de que una estrategia con sus parámetros por defecto sigue sosteniéndose fuera de muestra,
antes de confiarle más capital o promoverla a mainnet:

```bash
docker compose exec -e PYTHONPATH=/app app python scripts/validate_strategies.py
```

## Seguridad y red

- **Login obligatorio.** Usuario/contraseña fijos por `.env` (un solo operador, sin base de usuarios). La
  contraseña se guarda hasheada (PBKDF2-HMAC-SHA256, salt aleatorio), nunca en texto plano; se genera con
  `python -m app.tools.set_password`, que la pide por teclado sin mostrarla en pantalla. La sesión usa una
  cookie firmada (`APP_STORAGE_SECRET`, generar una sola vez). Tras 5 intentos fallidos seguidos, el login
  se bloquea 5 minutos y (si están configuradas) manda una alerta por Telegram.
- Postgres (5432) se publica solo en `127.0.0.1`; la app lo usa por la red interna de Docker.
- El dashboard escucha en `APP_BIND` (por defecto `0.0.0.0`, toda la red). Si el servidor tiene otras redes
  o salida a internet, poné `APP_BIND=127.0.0.1` y accedé por túnel SSH/VPN, o cerrá el puerto en el firewall
  (el login reduce el riesgo de exponerlo, pero no lo elimina: seguí prefiriendo una red privada).
- Cambiá `DB_PASSWORD` antes del primer arranque. En una base ya creada, el cambio hay que hacerlo también dentro
  de Postgres: `ALTER USER <usuario> WITH PASSWORD '<nueva>';`. Con la contraseña por defecto la app avisa al
  iniciar y se niega a operar en mainnet así.
- Las API keys de Bybit (demo y mainnet) van solo en `.env` (está en `.gitignore`; en el host debería tener
  permisos `600`, legible solo por el usuario que corre Docker). Creá las keys **sin permiso de retiro**. El
  cambio de entorno demo/mainnet se hace desde el dashboard, nunca automáticamente: exige confirmación
  explícita y bloquea el cambio si hay instancias corriendo o posiciones abiertas.
- El contenedor de la app corre con un usuario sin privilegios (no root), con límites de memoria/CPU en
  `docker-compose.yml`, y CI corre `pip-audit` en cada push para detectar vulnerabilidades conocidas en las
  dependencias.

## Datos de demostración

Para ver los paneles llenos sin tocar la base real ni operar, hay una copia del dashboard con datos de ejemplo:

```bash
# una sola vez: crear la base de demostración y sembrarla
docker compose exec db psql -U <DB_USER> -d <DB_NAME> -c 'CREATE DATABASE trading_demo OWNER "<DB_USER>"'
docker compose --profile demo run --rm app-demo python -m app.tools.seed_demo

docker compose --profile demo up -d app-demo      # http://localhost:8081
docker compose --profile demo stop app-demo       # apagarla
```

Usa la base `trading_demo` y `LIVE_ENABLED=false`: no arranca runners ni permite encender instancias, así que
nunca envía órdenes. El sembrado borra y recrea todas las tablas y se niega a correr sobre una base cuyo nombre
no termine en `_demo`. Los datos son sintéticos (no son resultados reales).

### Datos de ejemplo en la base real (para una presentación puntual)

A diferencia de lo anterior, estas dos herramientas agregan datos a la base **real** (nunca la borran ni la
pisan) — pensadas para tener algo que mostrar en una demo sin esperar semanas de operativa real:

```bash
# Operaciones sorteadas al azar según una tasa de aciertos configurada (ver app/tools/demo_data.py)
docker compose exec app python -m app.tools.seed_presentation

# Backtest REAL (motor real, datos históricos reales) guardado como si fuera la operativa de una instancia
docker compose exec app python -m app.tools.seed_real_backtest_instance <estrategia> <símbolo> <timeframe> <días> [nombre]
# ejemplo: docker compose exec app python -m app.tools.seed_real_backtest_instance sma_cross BTCUSDT 15 90
```

Las dos crean instancias **apagadas** (`is_active=False`): el orquestador nunca las enciende ni manda órdenes
con esa configuración. Las dos se niegan a correr si el nombre de instancia ya existe (nunca pisan una fila
real) y nunca tocan `bot_settings`. Hacé un `pg_dump` antes si vas a usarlas (ver sección Mantenimiento del
manual de uso) — no son destructivas, pero es una buena práctica antes de escribir en la base real.

## Operativa en vivo: reglas

- Una sola instancia activa por símbolo (Bybit tiene una posición por símbolo y cuenta).
- Al encender una instancia se espera a la próxima vela cerrada: no opera con señales anteriores al encendido.
- Los límites de riesgo (kill-switch, pérdida diaria por instancia y global, posiciones simultáneas,
  apalancamiento máximo) se revisan antes de cada operación nueva; ninguna estrategia puede saltearlos.
- El botón "Cerrar todo" (Configuración) cierra YA, con orden de mercado, cualquier posición abierta de
  cualquier instancia, esté corriendo o no.

## Tests

```bash
python -m venv .venv
./.venv/Scripts/activate       # Windows
pip install -r requirements.txt
pytest
```

La mayoría de los tests son puros (estrategias, backtester, exports, reglas de riesgo) y no requieren base de
datos. Un puñado de tests de integración (`tests/test_runner_live.py`, `tests/test_environment.py`) necesitan
una Postgres real y se saltean solos si no la encuentran; para correrlos de verdad:

```bash
docker compose exec app python -m pytest tests/test_runner_live.py tests/test_environment.py -q
```

CI (GitHub Actions, ver `.github/workflows/tests.yml`) corre la suite completa contra un Postgres de servicio en
cada push.

## Estructura

```
app/
  config.py             Configuración vía .env (pydantic-settings)
  auth.py                Login del dashboard (hash de contraseña, bloqueo por intentos, middleware)
  db/                    Modelos SQLAlchemy y conexión a Postgres
  exchange/              Wrapper sobre pybit (Bybit)
  strategies/
    base.py              Interfaz Strategy / Signal / StrategyContext
    registry.py          Registro automático de estrategias
    examples/            Estrategias de ejemplo (SMA, RSI, Donchian, funding+OI, trend pullback, volatility squeeze)
    ict/                 Estrategia ICT (barrida de liquidez + FVG)
  risk/                  Risk Manager y sizing de posiciones
  live/                  Ejecución en vivo: runner por instancia, orquestador, cierre de emergencia,
                         cambio de entorno demo/mainnet, alertas, eventos
  backtest/              Motor de backtest, métricas, Monte Carlo, walk-forward, sensibilidad, stress tests
  exports.py             Exportación a Excel/CSV (operativa en vivo y backtester)
  tools/                 CLI: set_password, seed_demo, seed_presentation, seed_real_backtest_instance
  ui/
    param_form.py         Genera formularios NiceGUI desde el schema pydantic de cada estrategia
    pages/                 Páginas del dashboard (incluye login.py)
  main.py                Arma la app FastAPI + NiceGUI
scripts/                 Herramientas de línea de comandos (validación de estrategias)
tests/                   Suite de pytest
docker-compose.yml
docker/Dockerfile
```

## Roadmap

- ✅ Motor de estrategias, backtester completo (Monte Carlo, walk-forward, sensibilidad, stress tests) y
  exportación de todos sus resultados.
- ✅ Operativa en vivo: Risk Manager, cierre de emergencia, alertas, comisiones/funding reales, apalancamiento
  dinámico, selector de entorno demo/mainnet.
- ✅ Primera validación fuera de muestra de las 7 estrategias por defecto (`scripts/validate_strategies.py`):
  la mayoría no se sostiene con sus parámetros de fábrica sobre ~2 años de BTCUSDT — re-optimizar y volver a
  validar antes de confiarles más capital o mainnet es el siguiente paso ahí, por separado de este roadmap.
- ✅ Presentación del proyecto: README, licencia y CI.
- ✅ Backtest directo desde una instancia y precio/PnL en vivo de posiciones abiertas en Resumen.
- ✅ Healthcheck de `app`/`app-demo` (`GET /health`, confirma que el proceso responde y puede hablar con la
  base) y remoción de Redis (estaba declarado pero sin ningún uso en el código).
- ✅ Pulido de UI: tarjetas resumen en Operaciones, catálogo/formulario/tarjetas de Estrategias, scroll
  horizontal consistente en tablas anchas.
- ✅ Login del dashboard, contenedor sin privilegios de root, límites de recursos en Docker, escaneo de
  vulnerabilidades de dependencias en CI y defensa en profundidad contra XSS.
- Pendiente: revisión de mobile a fondo (los grids ya son responsivos, falta probarlo en un dispositivo
  real), backups automatizados de la base, migrar los parches manuales de esquema (`app/db/base.py`) a
  Alembic.
