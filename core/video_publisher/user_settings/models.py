"""Settings schema. Secrets are NOT part of it: they live in the secret store."""

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Must match the locale files in ui/src/locales.
Language = Literal["ru", "en"]

# @username (5-32 chars, starts with a letter) or a numeric channel id like -1001234567890.
_CHANNEL_RE = re.compile(r"^(@[A-Za-z][A-Za-z0-9_]{4,31}|-100\d{5,})$")


class TelegramMode(StrEnum):
    BOT = "bot"
    ACCOUNT = "account"


def _check_channel(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    if not _CHANNEL_RE.match(value):
        raise ValueError("invalid_channel")
    return value


class TelegramSettings(BaseModel):
    mode: TelegramMode = TelegramMode.BOT
    channel: str | None = None
    api_id: int | None = Field(default=None, gt=0)

    _channel = field_validator("channel")(_check_channel)


class AppSettings(BaseModel):
    language: Language = "ru"
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)


class TelegramPatch(BaseModel):
    """Partial update; fields that are not sent stay unchanged, ``null`` clears a value."""

    mode: TelegramMode | None = None
    channel: str | None = None
    api_id: int | None = Field(default=None, gt=0)

    _channel = field_validator("channel")(_check_channel)


class SettingsPatch(BaseModel):
    language: Language | None = None
    telegram: TelegramPatch | None = None
