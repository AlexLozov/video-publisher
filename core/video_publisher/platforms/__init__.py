"""Platform adapters: one adapter per social network behind a common interface."""

from video_publisher.platforms.base import PlatformAdapter, PublishResult, ValidationResult
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.facebook_page import FacebookPageAdapter
from video_publisher.platforms.registry import PlatformRegistry, registry
from video_publisher.platforms.telegram_account import TelegramAccountAdapter
from video_publisher.platforms.telegram_bot import TelegramBotAdapter
from video_publisher.platforms.youtube import YouTubeAdapter

# Platforms that can publish. The UI shows the others as "in development".
registry.register(TelegramBotAdapter)
registry.register(FacebookPageAdapter)
registry.register(YouTubeAdapter)

__all__ = [
    "FacebookPageAdapter",
    "PlatformAdapter",
    "PlatformRegistry",
    "PublishError",
    "PublishResult",
    "TelegramAccountAdapter",
    "TelegramBotAdapter",
    "ValidationResult",
    "YouTubeAdapter",
    "registry",
]
