"""Test doubles shared by several test modules."""

from datetime import datetime

from video_publisher.domain import Platform, PostFormat, Publication, Video
from video_publisher.platforms import PlatformAdapter, PublishResult, ValidationResult


class FakeTelegram(PlatformAdapter):
    platform = Platform.TELEGRAM
    supported_formats = (PostFormat.VIDEO,)

    @property
    def supports_native_scheduling(self) -> bool:
        return True

    def authenticate(self) -> None:
        pass

    def validate(self, video: Video) -> ValidationResult:
        return ValidationResult(ok=True)

    def publish(self, publication: Publication) -> PublishResult:
        return PublishResult(external_id="1")

    def schedule(self, publication: Publication, at: datetime) -> PublishResult:
        return PublishResult(external_id="2")
