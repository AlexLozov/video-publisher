"""Runtime configuration loaded from environment variables and an optional .env file.

This is process-level configuration (paths, ports). Settings the user edits in the app
live in the database, see ``video_publisher.user_settings``.
"""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every field can be overridden with a ``VP_`` env variable."""

    model_config = SettingsConfigDict(env_prefix="VP_", env_file=".env", extra="ignore")

    app_name: str = "Video Publisher"
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path.home() / ".video-publisher"
    ui_dist_dir: Path | None = None
    # "memory" keeps secrets in RAM only; used by tests and E2E runs, never in the real app.
    secret_backend: Literal["keyring", "memory"] = "keyring"
    # Overridden only by E2E tests, which point it at a local fake Telegram server.
    telegram_api_base: str = "https://api.telegram.org"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"


def get_settings() -> Settings:
    """Return a fresh settings instance."""
    return Settings()
