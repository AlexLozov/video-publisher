from collections.abc import Iterator

import keyring
import keyring.errors
import pytest
from keyring.backend import KeyringBackend

from video_publisher.auth.secret_store import (
    KEYRING_SERVICE,
    KeyringSecretStore,
    MemorySecretStore,
    SecretName,
    SecretStoreError,
)


class FakeKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self.data[(service, username)]
        except KeyError:
            raise keyring.errors.PasswordDeleteError("absent") from None


class BrokenKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringLocked("locked")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.PasswordSetError("denied")

    def delete_password(self, service: str, username: str) -> None:
        raise keyring.errors.KeyringError("boom")


@pytest.fixture
def fake_keyring() -> Iterator[FakeKeyring]:
    previous = keyring.get_keyring()
    backend = FakeKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def broken_keyring() -> Iterator[None]:
    previous = keyring.get_keyring()
    keyring.set_keyring(BrokenKeyring())
    yield
    keyring.set_keyring(previous)


def test_keyring_roundtrip(fake_keyring: FakeKeyring) -> None:
    store = KeyringSecretStore()
    assert store.get(SecretName.TELEGRAM_BOT_TOKEN) is None
    store.set(SecretName.TELEGRAM_BOT_TOKEN, "secret")
    assert store.get(SecretName.TELEGRAM_BOT_TOKEN) == "secret"
    assert fake_keyring.data == {(KEYRING_SERVICE, "telegram_bot_token"): "secret"}
    store.delete(SecretName.TELEGRAM_BOT_TOKEN)
    assert store.get(SecretName.TELEGRAM_BOT_TOKEN) is None


def test_keyring_delete_missing_is_noop(fake_keyring: FakeKeyring) -> None:
    KeyringSecretStore().delete(SecretName.TELEGRAM_API_HASH)


@pytest.mark.usefixtures("broken_keyring")
def test_keyring_errors_are_wrapped() -> None:
    store = KeyringSecretStore()
    with pytest.raises(SecretStoreError):
        store.get(SecretName.TELEGRAM_BOT_TOKEN)
    with pytest.raises(SecretStoreError):
        store.set(SecretName.TELEGRAM_BOT_TOKEN, "x")
    with pytest.raises(SecretStoreError):
        store.delete(SecretName.TELEGRAM_BOT_TOKEN)


def test_memory_store() -> None:
    store = MemorySecretStore()
    store.set(SecretName.TELEGRAM_SESSION, "s")
    assert store.get(SecretName.TELEGRAM_SESSION) == "s"
    store.delete(SecretName.TELEGRAM_SESSION)
    store.delete(SecretName.TELEGRAM_SESSION)
    assert store.get(SecretName.TELEGRAM_SESSION) is None
