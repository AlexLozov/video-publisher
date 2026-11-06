"""Platform adapters: one adapter per social network behind a common interface."""

from video_publisher.platforms.base import PlatformAdapter, PublishResult, ValidationResult
from video_publisher.platforms.registry import PlatformRegistry, registry

__all__ = [
    "PlatformAdapter",
    "PlatformRegistry",
    "PublishResult",
    "ValidationResult",
    "registry",
]
