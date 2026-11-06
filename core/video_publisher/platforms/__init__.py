"""Platform adapters: one adapter per social network behind a common interface."""

from video_publisher.platforms.base import PlatformAdapter, PublishResult, ValidationResult
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.registry import PlatformRegistry, registry
from video_publisher.platforms.telegram_account import TelegramAccountAdapter
from video_publisher.platforms.telegram_bot import TelegramBotAdapter

# Platforms that can publish. The UI shows the others as "in development".
registry.register(TelegramBotAdapter)

__all__ = [
    "PlatformAdapter",
    "PlatformRegistry",
    "PublishError",
    "PublishResult",
    "TelegramAccountAdapter",
    "TelegramBotAdapter",
    "ValidationResult",
    "registry",
]
