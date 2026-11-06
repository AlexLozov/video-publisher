"""Abstract interface every platform adapter implements."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, Field

from video_publisher.domain import Platform, PostFormat, Publication, Video


class ValidationResult(BaseModel):
    """Outcome of checking a video against platform limits."""

    ok: bool
    errors: list[str] = Field(default_factory=list)


class PublishResult(BaseModel):
    """Outcome of a publish or schedule call."""

    external_id: str | None = None
    url: str | None = None


class PlatformAdapter(ABC):
    """Base class for a social network integration."""

    platform: ClassVar[Platform]
    supported_formats: ClassVar[tuple[PostFormat, ...]] = ()

    @property
    @abstractmethod
    def supports_native_scheduling(self) -> bool:
        """True if the platform itself can hold a post until a given time."""

    @abstractmethod
    def authenticate(self) -> None:
        """Make sure the adapter has valid credentials, refreshing them if needed."""

    @abstractmethod
    def validate(self, video: Video) -> ValidationResult:
        """Check that the video meets the platform requirements."""

    @abstractmethod
    def publish(self, publication: Publication) -> PublishResult:
        """Publish right now."""

    @abstractmethod
    def schedule(self, publication: Publication, at: datetime) -> PublishResult:
        """Schedule publication on the platform side (only if natively supported)."""
