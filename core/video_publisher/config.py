"""Application settings loaded from environment variables and an optional .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every field can be overridden with a ``VP_`` env variable."""

    model_config = SettingsConfigDict(env_prefix="VP_", env_file=".env", extra="ignore")

    app_name: str = "Video Publisher"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path.home() / ".video-publisher"
    ui_dist_dir: Path | None = None


def get_settings() -> Settings:
    """Return a fresh settings instance."""
    return Settings()
