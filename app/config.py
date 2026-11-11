from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Baraly API"
    environment: Literal["local", "test", "production"] = "local"
    log_level: str = "INFO"
    cors_origins: list[str] = []

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str

    jwt_secret: SecretStr = Field(min_length=32)
    access_token_ttl_minutes: int = Field(default=30, gt=0)
    refresh_token_ttl_days: int = Field(default=30, gt=0)

    # Допустимые значения aud в id_token: client ID для web, iOS и Android.
    google_client_ids: list[str] = []
    apple_client_ids: list[str] = []

    # S3-совместимое хранилище картинок (MinIO).
    s3_endpoint: str = "localhost:9000"
    s3_access_key: str
    s3_secret_key: SecretStr
    s3_bucket: str = "baraly"
    s3_secure: bool = False
    s3_region: str = "us-east-1"
    # Адрес, по которому браузер получает картинки.
    s3_public_url: str = "http://localhost:9000"
    max_image_size_mb: int = Field(default=10, gt=0)

    # Сколько минут места удерживаются за пользователем до оплаты.
    booking_hold_minutes: int = Field(default=15, gt=0, le=120)
    # fake: тестовая оплата без денег, только для разработки.
    payment_provider: Literal["fake"] = "fake"
    public_api_url: str = "http://localhost:8000"

    @model_validator(mode="after")
    def _forbid_dev_shortcuts_in_production(self) -> "Settings":
        if self.environment != "production":
            return self
        if self.payment_provider == "fake":
            raise ValueError("Configure a real payment provider for production")
        return self

    @property
    def docs_enabled(self) -> bool:
        return self.environment != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
