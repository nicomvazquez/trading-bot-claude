from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "trading_bot"
    db_user: str = "trading_bot"
    db_password: str = "changeme"

    redis_host: str = "localhost"
    redis_port: int = 6379

    bybit_env: str = "demo"  # "demo" (demo trading, datos reales + fondos virtuales) | "mainnet" (real)
    bybit_api_key: str = ""
    bybit_api_secret: str = ""

    app_port: int = 8080

    # False: copia de demostracion con datos de ejemplo. No arranca runners ni permite encender instancias,
    # asi que nunca envia ordenes al exchange (ver docker-compose, servicio app-demo).
    live_enabled: bool = True

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
