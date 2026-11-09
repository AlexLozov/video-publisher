"""Every error code the backend can send must have a message in every UI language."""

import json
import re
from pathlib import Path

import pytest

from video_publisher.api.errors import HTTP_STATUS_CODES

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core" / "video_publisher"
LOCALES = sorted((ROOT / "ui" / "src" / "locales").glob("*.json"))

# Codes produced indirectly (validate() lists, fixed strings in handlers and services).
EXTRA_ERROR_CODES = {
    "validation_error",
    "internal_error",
    "http_error",
    "interrupted",
    "video_not_found",
    "telegram_unsupported_format",
    "telegram_bot_file_too_large",
    "telegram_rejected",
    "telegram_invalid_token",
    "telegram_chat_not_found",
    "telegram_bot_not_admin",
    "telegram_account_file_too_large",
    "facebook_unsupported_format",
    "facebook_file_too_large",
    "facebook_token_invalid",
    "facebook_rate_limited",
    "facebook_unavailable",
    "facebook_permission_denied",
    "facebook_video_rejected",
    "facebook_page_not_found",
    "facebook_rejected",
    "youtube_unsupported_format",
    "youtube_file_too_large",
    "youtube_port_busy",
    "youtube_quota_exceeded",
    "youtube_upload_limit",
    "youtube_rate_limited",
    "youtube_api_disabled",
    "youtube_permission_denied",
    "youtube_video_not_found",
    "unknown",
    "network",
}


# Error names of the OAuth protocol that the code compares against, not codes it sends.
OAUTH_PROTOCOL_WORDS = {"invalid_grant", "invalid_client"}


def _source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in CORE.rglob("*.py"))


def backend_error_codes() -> set[str]:
    src = _source()
    found = set(re.findall(r'(?:PublishError|ApiError|InvalidSecretError)\(\s*"([a-z0-9_]+)"', src))
    found |= set(re.findall(r'"(invalid_[a-z_]+)"', src))
    codes = found | set(HTTP_STATUS_CODES.values()) | EXTRA_ERROR_CODES
    # field codes live under fieldErrors
    return codes - field_error_codes() - OAUTH_PROTOCOL_WORDS


def field_error_codes() -> set[str]:
    return set(re.findall(r'ValueError\("([a-z_]+)"\)', _source()))


def test_scanner_finds_codes() -> None:
    codes = backend_error_codes()
    assert {"telegram_timeout_uncertain", "secret_store_unavailable", "not_found"} <= codes


@pytest.mark.parametrize("locale", LOCALES, ids=lambda p: p.stem)
def test_every_backend_error_code_is_translated(locale: Path) -> None:
    messages = json.loads(locale.read_text(encoding="utf-8"))
    missing = sorted(backend_error_codes() - set(messages["errors"]))
    assert missing == []


@pytest.mark.parametrize("locale", LOCALES, ids=lambda p: p.stem)
def test_every_field_error_code_is_translated(locale: Path) -> None:
    messages = json.loads(locale.read_text(encoding="utf-8"))
    missing = sorted(field_error_codes() - set(messages["fieldErrors"]))
    assert missing == []


def test_locales_exist() -> None:
    assert [p.stem for p in LOCALES] == ["en", "ru"]
