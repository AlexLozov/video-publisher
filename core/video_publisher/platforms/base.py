"""Abstract interface every platform adapter implements."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import ClassVar

from pydantic import BaseModel, Field

from video_publisher.domain import Platform, PostFormat, Publication, Video
from video_publisher.platforms.errors import PublishError


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
    # How far ahead ``schedule`` accepts a time (only used with native scheduling).
    max_schedule_ahead: ClassVar[timedelta] = timedelta(0)
    # How soon ``schedule`` accepts a time; platforms may require more than the default.
    min_schedule_lead: ClassVar[timedelta] = timedelta(minutes=1)
    # Call ``authenticate`` before anything is uploaded, so a dead login stops the whole
    # upload (all networks) instead of failing later for this network only.
    check_login_before_start: ClassVar[bool] = False

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

    def check_text(self, publication: Publication) -> None:  # noqa: B027
        """Raise a ``PublishError`` if the title or text break the platform limits.

        Called before the upload starts, so the user can fix the text right away.
        """

    @abstractmethod
    def publish(self, publication: Publication) -> PublishResult:
        """Publish right now."""

    @abstractmethod
    def schedule(self, publication: Publication, at: datetime) -> PublishResult:
        """Schedule publication on the platform side (only if natively supported)."""

    def cancel_scheduled(self, external_id: str) -> None:
        """Withdraw a post that ``schedule`` handed to the platform and is not out yet."""
        raise PublishError("cancel_not_supported", platform=self.platform.value)
