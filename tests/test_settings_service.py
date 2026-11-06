from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect

from video_publisher.auth.secret_store import KeyringSecretStore, MemorySecretStore, SecretName
from video_publisher.config import Settings
from video_publisher.storage.models import SettingRow
from video_publisher.user_settings import (
    SettingsPatch,
    SettingsService,
    TelegramMode,
    TelegramPatch,
    build_settings_service,
)
from video_publisher.user_settings.service import InvalidSecretError

VALID_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
VALID_HASH = "0123456789abcdef0123456789abcdef"


def test_defaults_on_empty_db(settings_service: SettingsService) -> None:
    s = settings_service.get()
    assert s.language == "ru"
    assert s.telegram.mode is TelegramMode.BOT
    assert s.telegram.channel is None
    assert s.telegram.api_id is None


def test_update_persists_and_merges(settings_service: SettingsService) -> None:
    settings_service.update(SettingsPatch(language="en"))
    settings_service.update(SettingsPatch(telegram=TelegramPatch(channel="@my_channel")))
    settings_service.update(SettingsPatch(telegram=TelegramPatch(api_id=12345)))
    s = settings_service.get()
    assert s.language == "en"
    assert s.telegram.channel == "@my_channel"
    assert s.telegram.api_id == 12345
    assert s.telegram.mode is TelegramMode.BOT


def test_null_clears_value(settings_service: SettingsService) -> None:
    settings_service.update(SettingsPatch(telegram=TelegramPatch(channel="@my_channel")))
    patch = SettingsPatch.model_validate({"telegram": {"channel": None}})
    assert settings_service.update(patch).telegram.channel is None


def test_switch_mode(settings_service: SettingsService) -> None:
    patch = SettingsPatch(telegram=TelegramPatch(mode=TelegramMode.ACCOUNT))
    assert settings_service.update(patch).telegram.mode is TelegramMode.ACCOUNT
    assert settings_service.get().telegram.mode is TelegramMode.ACCOUNT


def test_explicit_null_mode_is_rejected(settings_service: SettingsService) -> None:
    patch = SettingsPatch.model_validate({"telegram": {"mode": None}})
    with pytest.raises(ValidationError):
        settings_service.update(patch)
    assert settings_service.get().telegram.mode is TelegramMode.BOT


@pytest.mark.parametrize(
    "channel", ["@abcde", "@My_Channel_2024", "-1001234567890", "  @padded_name  "]
)
def test_valid_channels(channel: str) -> None:
    assert TelegramPatch(channel=channel).channel == channel.strip()


@pytest.mark.parametrize(
    "channel", ["abc", "@abc", "@1channel", "https://t.me/x", "-123", "@" + "a" * 40, "@bad-name"]
)
def test_invalid_channels(channel: str) -> None:
    with pytest.raises(ValidationError, match="invalid_channel"):
        TelegramPatch(channel=channel)


def test_blank_channel_becomes_none() -> None:
    assert TelegramPatch(channel="   ").channel is None


@pytest.mark.parametrize("api_id", [0, -5])
def test_api_id_must_be_positive(api_id: int) -> None:
    with pytest.raises(ValidationError):
        TelegramPatch(api_id=api_id)


def test_unsupported_language_rejected() -> None:
    with pytest.raises(ValidationError):
        SettingsPatch.model_validate({"language": "de"})


def test_corrupt_stored_value_falls_back_to_default(settings_service: SettingsService) -> None:
    settings_service.update(SettingsPatch(language="en"))
    with settings_service._sessions.begin() as session:
        session.merge(SettingRow(key="telegram", value={"mode": "carrier_pigeon"}))
        session.merge(SettingRow(key="obsolete_key", value=1))
    s = settings_service.get()
    assert s.language == "en"  # valid value kept
    assert s.telegram.mode is TelegramMode.BOT  # broken value replaced by default


def test_secret_status_and_roundtrip(
    settings_service: SettingsService, secrets: MemorySecretStore
) -> None:
    assert not any(settings_service.secret_status().values())
    settings_service.set_secret(SecretName.TELEGRAM_BOT_TOKEN, f"  {VALID_TOKEN}\n")
    assert secrets.get(SecretName.TELEGRAM_BOT_TOKEN) == VALID_TOKEN
    assert settings_service.get_secret(SecretName.TELEGRAM_BOT_TOKEN) == VALID_TOKEN
    assert settings_service.secret_status()[SecretName.TELEGRAM_BOT_TOKEN] is True
    settings_service.delete_secret(SecretName.TELEGRAM_BOT_TOKEN)
    assert settings_service.secret_status()[SecretName.TELEGRAM_BOT_TOKEN] is False


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        (SecretName.TELEGRAM_BOT_TOKEN, "not-a-token", "invalid_bot_token"),
        (SecretName.TELEGRAM_BOT_TOKEN, "123:short", "invalid_bot_token"),
        (SecretName.TELEGRAM_API_HASH, "xyz", "invalid_api_hash"),
        (SecretName.TELEGRAM_API_HASH, VALID_HASH.upper(), "invalid_api_hash"),
    ],
)
def test_invalid_secret_formats(
    settings_service: SettingsService, name: SecretName, value: str, code: str
) -> None:
    with pytest.raises(InvalidSecretError) as info:
        settings_service.set_secret(name, value)
    assert info.value.code == code
    assert settings_service.secret_status()[name] is False


def test_api_hash_accepted(settings_service: SettingsService) -> None:
    settings_service.set_secret(SecretName.TELEGRAM_API_HASH, VALID_HASH)
    assert settings_service.secret_status()[SecretName.TELEGRAM_API_HASH] is True


def test_secrets_never_stored_in_database(settings_service: SettingsService, db_url: str) -> None:
    settings_service.set_secret(SecretName.TELEGRAM_BOT_TOKEN, VALID_TOKEN)
    settings_service.update(SettingsPatch(language="en"))
    with settings_service._sessions() as session:
        dump = repr([(r.key, r.value) for r in session.query(SettingRow).all()])
    assert VALID_TOKEN not in dump


def test_build_settings_service_creates_db(tmp_path: Path) -> None:
    config = Settings(data_dir=tmp_path / "nested" / "dir", secret_backend="memory")
    service = build_settings_service(config)
    assert (tmp_path / "nested" / "dir" / "app.db").is_file()
    assert service.get().language == "ru"
    engine = service._sessions.kw["bind"]
    assert "settings" in inspect(engine).get_table_names()
    engine.dispose()


def test_build_settings_service_uses_keyring_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VP_SECRET_BACKEND")
    service = build_settings_service(Settings(data_dir=tmp_path))
    assert isinstance(service._secrets, KeyringSecretStore)
    service._sessions.kw["bind"].dispose()
