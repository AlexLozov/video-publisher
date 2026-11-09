"""Secret storage. Secrets never go into the database or log files.

The real app uses the OS credential store via ``keyring`` (Windows Credential Manager).
Tests and E2E runs use ``MemorySecretStore``.
"""

from enum import StrEnum
from typing import Protocol

import keyring
import keyring.errors

KEYRING_SERVICE = "video-publisher"


class SecretName(StrEnum):
    TELEGRAM_BOT_TOKEN = "telegram_bot_token"
    TELEGRAM_API_HASH = "telegram_api_hash"
    # Written by the Telegram login flow, never directly by the user.
    TELEGRAM_SESSION = "telegram_session"
    FACEBOOK_APP_SECRET = "facebook_app_secret"
    # Written by the Facebook login flow: a page token that does not expire.
    FACEBOOK_PAGE_TOKEN = "facebook_page_token"


class SecretStoreError(Exception):
    """The OS credential store is unavailable or refused the operation."""


class SecretStore(Protocol):
    def get(self, name: SecretName) -> str | None: ...

    def set(self, name: SecretName, value: str) -> None: ...

    def delete(self, name: SecretName) -> None: ...


class KeyringSecretStore:
    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self._service = service

    def get(self, name: SecretName) -> str | None:
        try:
            return keyring.get_password(self._service, name.value)
        except keyring.errors.KeyringError as exc:
            raise SecretStoreError(str(exc)) from exc

    def set(self, name: SecretName, value: str) -> None:
        try:
            keyring.set_password(self._service, name.value, value)
        except keyring.errors.KeyringError as exc:
            raise SecretStoreError(str(exc)) from exc

    def delete(self, name: SecretName) -> None:
        try:
            keyring.delete_password(self._service, name.value)
        except keyring.errors.PasswordDeleteError:
            pass  # already absent
        except keyring.errors.KeyringError as exc:
            raise SecretStoreError(str(exc)) from exc


class MemorySecretStore:
    def __init__(self) -> None:
        self._data: dict[SecretName, str] = {}

    def get(self, name: SecretName) -> str | None:
        return self._data.get(name)

    def set(self, name: SecretName, value: str) -> None:
        self._data[name] = value

    def delete(self, name: SecretName) -> None:
        self._data.pop(name, None)
