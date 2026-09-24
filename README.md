# Bot Trading - Bybit Futures

Bot de trading algorítmico para futuros perpetuos de Bybit, con estrategias
configurables en Python, backtesting y un dashboard en vivo. Todo en un solo
proceso Python (FastAPI + NiceGUI).

## Estado actual (Fase 0 / inicio Fase 1)

- Estructura del proyecto y Docker Compose (Postgres/TimescaleDB + Redis + app).
- Interfaz `Strategy` común para live y backtest, con registro automático de
  estrategias y generación automática de formularios de parámetros en el
  dashboard.
- Estrategia de ejemplo: cruce de medias móviles (`sma_cross`).
- Dashboard con 5 páginas: Overview, Operaciones, Estrategias, Backtesting,
  Configuración. Las secciones de ejecución en vivo y backtest todavía son
  placeholders (Fases 2-3).
- Conector a Bybit (`pybit`) con chequeo de conexión desde la página de
  Configuración.

## Requisitos

- Docker Desktop (Postgres/TimescaleDB + Redis + la app corren en contenedores).
- Una API key/secret de **Demo Trading** de Bybit: se crea desde tu cuenta
  normal en bybit.com (perfil → API → Create New Key), activando el toggle
  "Demo Trading" antes de generarla. No es una cuenta aparte, pero la key
  es distinta de la de mainnet. Demo Trading corre sobre el mismo precio y
  la misma liquidez que producción, con fondos virtuales.

## Cómo correrlo

```bash
cp .env.example .env
# completar BYBIT_API_KEY y BYBIT_API_SECRET en .env con las de Demo Trading

docker compose up --build
```

Abrir http://localhost:8080

## Seguridad y red

El dashboard **no tiene login**: está pensado para correr en un servidor privado.

- Postgres (5432) y Redis (6379) se publican solo en `127.0.0.1`; la app los usa por la red interna de Docker.
- El dashboard escucha en `APP_BIND` (por defecto `0.0.0.0`, toda la red). Si el servidor tiene otras redes
  o salida a internet, poné `APP_BIND=127.0.0.1` y accedé por túnel SSH/VPN, o cerrá el puerto en el firewall.
- Cambiá `DB_PASSWORD` antes del primer arranque. En una base ya creada, el cambio hay que hacerlo también dentro
  de Postgres: `ALTER USER <usuario> WITH PASSWORD '<nueva>';`. Con la contraseña por defecto la app avisa al
  iniciar y, en MAINNET, se niega a arrancar.
- Las API keys de Bybit van solo en `.env` (está en `.gitignore`). Creá la key **sin permiso de retiro**.

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

## Operativa en vivo: reglas

- Una sola instancia activa por símbolo (Bybit tiene una posición por símbolo y cuenta).
- Al encender una instancia se espera a la próxima vela cerrada: no opera con señales anteriores al encendido.

## Desarrollo local sin Docker (solo para tests unitarios)

```bash
python -m venv .venv
./.venv/Scripts/activate       # Windows
pip install -r requirements.txt
pytest
```

Los tests de estrategias (`tests/test_strategies.py`) no requieren base de
datos. Correr la app completa sí requiere Postgres/Redis, por eso se usa
Docker Compose.

## Estructura

```
app/
  config.py            Configuración vía .env (pydantic-settings)
  db/                   Modelos SQLAlchemy y conexión a Postgres
  exchange/             Wrapper sobre pybit (Bybit)
  strategies/
    base.py             Interfaz Strategy / Signal / StrategyContext
    registry.py         Registro automático de estrategias
    examples/           Estrategias de ejemplo (sma_cross)
  ui/
    param_form.py        Genera formularios NiceGUI desde el schema pydantic
                          de cada estrategia
    pages/               Páginas del dashboard
  main.py               Arma la app FastAPI + NiceGUI
docker-compose.yml
docker/Dockerfile
```

## Roadmap

0. ✅ Fundaciones (repo, Docker, conector Bybit).
1. ✅ Motor de estrategias (interfaz común, registro automático, formularios
   auto-generados). Estrategias: SMA cross, RSI reversion, ICT (barrida +
   FVG).
2. ✅ Backtesting (datos históricos, métricas, gráficos, Monte Carlo, barrido
   de parámetros).
3. 🚧 Ejecución en vivo en Demo Trading (Risk Manager, Order Manager, loop
   multi-instancia) — en curso.
4. Dashboard completo: Overview y Operaciones con datos reales.
5. Multi-estrategia y hardening: alertas (Telegram/email), logs.
6. Paso a mainnet.
