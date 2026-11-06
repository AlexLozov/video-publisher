"""Core domain entities: what is published, where and in which format."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Platform(StrEnum):
    """Supported social networks."""

    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TELEGRAM = "telegram"


class PostFormat(StrEnum):
    """How a video is presented on a platform."""

    SHORT = "short"
    REELS = "reels"
    POST = "post"
    VIDEO = "video"


class PublicationStatus(StrEnum):
    """Lifecycle of a single publication."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class Video(BaseModel):
    """A ready-to-publish video file on disk."""

    id: UUID = Field(default_factory=uuid4)
    path: Path
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None


class PostText(BaseModel):
    """Caption and metadata attached to a post."""

    title: str = ""
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)


class PublishTarget(BaseModel):
    """Where to publish: a platform, an account/channel on it and a format."""

    platform: Platform
    format: PostFormat
    account_id: str | None = None


class Publication(BaseModel):
    """One video published to one target, possibly at a scheduled time."""

    id: UUID = Field(default_factory=uuid4)
    video: Video
    target: PublishTarget
    text: PostText = Field(default_factory=PostText)
    scheduled_at: datetime | None = None
    status: PublicationStatus = PublicationStatus.DRAFT
    external_id: str | None = None
    error: str | None = None
