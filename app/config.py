from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "trading_bot"
    db_user: str = "trading_bot"
    db_password: str = "changeme"

    # Entorno con el que arranca el proceso. Una vez arriba, el entorno REALMENTE activo lo decide
    # BotSettings.bybit_env (elegido desde Configuración) — ver app.live.environment.sync_from_db,
    # que lo aplica en el arranque y lo mantiene si ya se habia elegido mainnet antes de un reinicio.
    bybit_env: str = "demo"  # "demo" (demo trading, datos reales + fondos virtuales) | "mainnet" (real)
    bybit_api_key: str = ""  # credenciales de Demo Trading
    bybit_api_secret: str = ""
    bybit_mainnet_api_key: str = ""  # credenciales de MAINNET (dinero real). Vacias = no se puede elegir mainnet.
    bybit_mainnet_api_secret: str = ""

    app_port: int = 8080
    # Zona horaria en la que se MUESTRAN las horas y se cuenta el "dia" (todo se guarda en UTC). Argentina: UTC-3.
    app_timezone: str = "America/Argentina/Buenos_Aires"

    # Login del dashboard (un solo operador). app_password_hash se genera con
    # `python -m app.tools.set_password`, nunca se guarda la contraseña en texto plano.
    # Vacio = el arranque falla (ver check_security en main.py): no hay dashboard sin login.
    app_user: str = "admin"
    app_password_hash: str = ""
    # Clave para firmar la cookie de sesion (NiceGUI storage_secret). Se genera una vez con
    # `python -c "import secrets; print(secrets.token_hex(32))"` y no deberia cambiar despues
    # (cambiarla invalida todas las sesiones activas).
    app_storage_secret: str = ""

    # False: copia de demostracion con datos de ejemplo. No arranca runners ni permite encender instancias,
    # asi que nunca envia ordenes al exchange (ver docker-compose, servicio app-demo).
    live_enabled: bool = True

    # Alertas por Telegram (kill-switch, errores de la operativa en vivo). Vacios = alertas desactivadas.
    # Se crean hablando con @BotFather (token) y @userinfobot o similar (chat_id, tu ID numerico de Telegram).
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def bybit_demo(self) -> bool:
        return self.bybit_env.lower() != "mainnet"


settings = Settings()
