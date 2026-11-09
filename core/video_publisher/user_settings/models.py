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


_APP_ID_RE = re.compile(r"^\d{5,20}$")


def _check_app_id(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    if not _APP_ID_RE.match(value):
        raise ValueError("invalid_app_id")
    return value


class FacebookSettings(BaseModel):
    """The Meta app is the user's own (App ID here, App Secret in the secret store).

    ``page_id`` and ``page_name`` are filled in by the login flow, not typed by the user.
    """

    app_id: str | None = None
    page_id: str | None = None
    page_name: str | None = None

    _app_id = field_validator("app_id")(_check_app_id)


_GOOGLE_CLIENT_ID_RE = re.compile(r"^\d{6,20}-[a-z0-9]{8,64}\.apps\.googleusercontent\.com$")

# YouTube video categories offered in Settings (ids are the same in every region).
YOUTUBE_CATEGORIES = (
    "1",  # Film & Animation
    "2",  # Autos & Vehicles
    "10",  # Music
    "15",  # Pets & Animals
    "17",  # Sports
    "19",  # Travel & Events
    "20",  # Gaming
    "22",  # People & Blogs
    "23",  # Comedy
    "24",  # Entertainment
    "25",  # News & Politics
    "26",  # Howto & Style
    "27",  # Education
    "28",  # Science & Technology
    "29",  # Nonprofits & Activism
)
# YouTube limit for all tags together; a tag with a space counts with its quotes.
YOUTUBE_MAX_TAGS_CHARS = 500


class YouTubePrivacy(StrEnum):
    PUBLIC = "public"
    UNLISTED = "unlisted"
    PRIVATE = "private"


def _check_client_id(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    if not _GOOGLE_CLIENT_ID_RE.match(value):
        raise ValueError("invalid_client_id")
    return value


def _check_category(value: str | None) -> str | None:
    if value is not None and value not in YOUTUBE_CATEGORIES:
        raise ValueError("invalid_category")
    return value


def youtube_tags_length(tags: list[str]) -> int:
    """Length as YouTube counts it: commas between tags, quotes around tags with spaces."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


def _check_tags(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    tags: list[str] = []
    for raw in value:
        tag = raw.strip().lstrip("#").strip()
        if not tag or tag in tags:
            continue
        if "<" in tag or ">" in tag or "," in tag:
            raise ValueError("invalid_tag")
        tags.append(tag)
    if youtube_tags_length(tags) > YOUTUBE_MAX_TAGS_CHARS:
        raise ValueError("youtube_tags_too_long")
    return tags


class YouTubeSettings(BaseModel):
    """The OAuth client is the user's own Google Cloud project (Client ID here, the secret in
    the secret store). ``channel_*`` are filled in by the login flow. The rest are the
    defaults for every upload.
    """

    client_id: str | None = None
    channel_id: str | None = None
    channel_title: str | None = None
    privacy: YouTubePrivacy = YouTubePrivacy.PUBLIC
    made_for_kids: bool = False
    category_id: str = "22"
    tags: list[str] = Field(default_factory=list)

    _client_id = field_validator("client_id")(_check_client_id)
    _category = field_validator("category_id")(_check_category)
    _tags = field_validator("tags")(_check_tags)


class AppSettings(BaseModel):
    language: Language = "ru"
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    facebook: FacebookSettings = Field(default_factory=FacebookSettings)
    youtube: YouTubeSettings = Field(default_factory=YouTubeSettings)


class TelegramPatch(BaseModel):
    """Partial update; fields that are not sent stay unchanged, ``null`` clears a value."""

    mode: TelegramMode | None = None
    channel: str | None = None
    api_id: int | None = Field(default=None, gt=0)

    _channel = field_validator("channel")(_check_channel)


class FacebookPatch(BaseModel):
    """Only the App ID is edited by hand; the page comes from logging in."""

    app_id: str | None = None

    _app_id = field_validator("app_id")(_check_app_id)


class YouTubePatch(BaseModel):
    """Everything but the channel, which comes from logging in."""

    client_id: str | None = None
    privacy: YouTubePrivacy | None = None
    made_for_kids: bool | None = None
    category_id: str | None = None
    tags: list[str] | None = Field(default=None, max_length=100)

    _client_id = field_validator("client_id")(_check_client_id)
    _category = field_validator("category_id")(_check_category)
    _tags = field_validator("tags")(_check_tags)


class SettingsPatch(BaseModel):
    language: Language | None = None
    telegram: TelegramPatch | None = None
    facebook: FacebookPatch | None = None
    youtube: YouTubePatch | None = None
