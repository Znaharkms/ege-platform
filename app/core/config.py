from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="EGE_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://ege:ege_dev_password@localhost:5432/ege"
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = Field(default=15, ge=5, le=60)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=180)
    email_code_ttl_minutes: int = Field(default=10, ge=2, le=30)
    admin_notification_email: str = ""
    public_base_url: str = "http://127.0.0.1:8000"
    smtp_host: str | None = None
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_ssl: bool = True
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    refresh_cookie_name: str = "refresh_token"
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    openapi_contract_path: Path = Path("../api/openapi.yaml")
    media_root: Path = Path("../data/media")
    max_image_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)

    @property
    def secure_cookies(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
