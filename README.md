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
