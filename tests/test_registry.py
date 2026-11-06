import pytest

from fakes import FakeTelegram
from video_publisher.domain import Platform
from video_publisher.platforms import PlatformAdapter, PlatformRegistry


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
