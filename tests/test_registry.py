from datetime import datetime

import pytest

from video_publisher.domain import Platform, PostFormat, Publication, Video
from video_publisher.platforms import (
    PlatformAdapter,
    PlatformRegistry,
    PublishResult,
    ValidationResult,
)


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


def test_register_and_get() -> None:
    reg = PlatformRegistry()
    reg.register(FakeTelegram)
    assert reg.get(Platform.TELEGRAM) is FakeTelegram
    assert reg.is_registered(Platform.TELEGRAM)
    assert not reg.is_registered(Platform.YOUTUBE)
    assert reg.registered() == [Platform.TELEGRAM]


def test_duplicate_registration_fails() -> None:
    reg = PlatformRegistry()
    reg.register(FakeTelegram)
    with pytest.raises(ValueError):
        reg.register(FakeTelegram)


def test_missing_adapter_raises_key_error() -> None:
    with pytest.raises(KeyError):
        PlatformRegistry().get(Platform.YOUTUBE)


def test_base_class_is_abstract() -> None:
    with pytest.raises(TypeError):
        PlatformAdapter()  # type: ignore[abstract]
