"""Reading and writing user settings and secrets."""

import logging
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from video_publisher.auth.secret_store import (
    KeyringSecretStore,
    MemorySecretStore,
    SecretName,
    SecretStore,
)
from video_publisher.config import Settings
from video_publisher.storage import create_db_engine, create_session_factory, upgrade_database
from video_publisher.storage.models import SettingRow
from video_publisher.user_settings.models import AppSettings, SettingsPatch

log = logging.getLogger(__name__)

_SECRET_FORMATS: dict[SecretName, tuple[re.Pattern[str], str]] = {
    SecretName.TELEGRAM_BOT_TOKEN: (
        re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$"),
        "invalid_bot_token",
    ),
    SecretName.TELEGRAM_API_HASH: (re.compile(r"^[0-9a-f]{32}$"), "invalid_api_hash"),
}


class InvalidSecretError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class SettingsService:
    def __init__(self, session_factory: sessionmaker[Session], secrets: SecretStore) -> None:
        self._sessions = session_factory
        self._secrets = secrets

    @property
    def session_factory(self) -> sessionmaker[Session]:
        return self._sessions

    # --- settings -------------------------------------------------------------------

    def get(self) -> AppSettings:
        with self._sessions() as session:
            rows = session.scalars(select(SettingRow)).all()
            stored = {row.key: row.value for row in rows}
        return self._parse(stored)

    def update(self, patch: SettingsPatch) -> AppSettings:
        current = self.get().model_dump(mode="json")
        merged = _deep_merge(current, patch.model_dump(mode="json", exclude_unset=True))
        new = AppSettings.model_validate(merged)
        with self._sessions.begin() as session:
            for key, value in new.model_dump(mode="json").items():
                session.merge(SettingRow(key=key, value=value))
        return new

    @staticmethod
    def _parse(stored: dict[str, Any]) -> AppSettings:
        """Validate stored data; a broken or outdated value falls back to its default."""
        known = {k: v for k, v in stored.items() if k in AppSettings.model_fields}
        try:
            return AppSettings.model_validate(known)
        except ValidationError:
            good: dict[str, Any] = {}
            for key, value in known.items():
                try:
                    AppSettings.model_validate({key: value})
                    good[key] = value
                except ValidationError:
                    log.warning("Ignoring invalid stored setting %r", key)
            return AppSettings.model_validate(good)

    # --- secrets --------------------------------------------------------------------

    def secret_status(self) -> dict[SecretName, bool]:
        return {name: self._secrets.get(name) is not None for name in SecretName}

    def set_secret(self, name: SecretName, value: str) -> None:
        value = value.strip()
        fmt = _SECRET_FORMATS.get(name)
        if fmt is not None and not fmt[0].match(value):
            raise InvalidSecretError(fmt[1])
        self._secrets.set(name, value)

    def delete_secret(self, name: SecretName) -> None:
        self._secrets.delete(name)

    def get_secret(self, name: SecretName) -> str | None:
        """For platform adapters only; never expose through the API."""
        return self._secrets.get(name)


def build_settings_service(config: Settings) -> SettingsService:
    """Create the data dir, migrate the database and wire the secret store."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    upgrade_database(config.database_url)
    engine = create_db_engine(config.database_url)
    secrets: SecretStore = (
        MemorySecretStore() if config.secret_backend == "memory" else KeyringSecretStore()
    )
    return SettingsService(create_session_factory(engine), secrets)
