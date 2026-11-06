"""Registry mapping each platform to its adapter class."""

from video_publisher.domain import Platform
from video_publisher.platforms.base import PlatformAdapter


class PlatformRegistry:
    """Keeps track of which platforms have an adapter implementation."""

    def __init__(self) -> None:
        self._adapters: dict[Platform, type[PlatformAdapter]] = {}

    def register(self, adapter_cls: type[PlatformAdapter]) -> type[PlatformAdapter]:
        """Register an adapter class. Usable as a class decorator."""
        platform = adapter_cls.platform
        if platform in self._adapters:
            raise ValueError(f"Adapter for {platform} is already registered")
        self._adapters[platform] = adapter_cls
        return adapter_cls

    def get(self, platform: Platform) -> type[PlatformAdapter]:
        """Return the adapter class for a platform or raise KeyError."""
        try:
            return self._adapters[platform]
        except KeyError:
            raise KeyError(f"No adapter registered for {platform}") from None

    def is_registered(self, platform: Platform) -> bool:
        return platform in self._adapters

    def registered(self) -> list[Platform]:
        """Platforms that have an adapter, in enum order."""
        return [p for p in Platform if p in self._adapters]


registry = PlatformRegistry()
