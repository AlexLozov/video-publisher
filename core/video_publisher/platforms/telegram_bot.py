"""Telegram channel publishing through a bot (Bot API ``sendVideo``).

Limits of the hosted Bot API: uploads up to 50 MB, no native scheduled posts (the app's
scheduler publishes at the right time instead).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import httpx

from video_publisher.domain import Platform, PostFormat, PostText, Publication, Video
from video_publisher.platforms.base import PlatformAdapter, PublishResult, ValidationResult
from video_publisher.platforms.errors import PublishError

API_BASE = "https://api.telegram.org"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_CAPTION = 1024
SUPPORTED_EXTENSIONS = frozenset({".mp4"})

# The bot token is part of every request URL; httpx logs URLs at INFO level.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Upload timeouts: connecting must be quick, writing a 50 MB file may take a while.
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=600.0, pool=10.0)


def build_caption(text: PostText) -> str:
    """Title, description and hashtags separated by blank lines."""
    parts = [text.title.strip(), text.description.strip()]
    tags = " ".join(
        tag if tag.startswith("#") else f"#{tag}"
        for tag in (t.strip() for t in text.hashtags)
        if tag
    )
    parts.append(tags)
    return "\n\n".join(p for p in parts if p)


def post_url(channel: str, message_id: int) -> str | None:
    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}/{message_id}"
    if channel.startswith("-100"):
        return f"https://t.me/c/{channel[4:]}/{message_id}"
    return None


def _classify(status: int, description: str) -> str:
    """Map a Bot API error to our error code."""
    d = description.lower()
    if status == 401:
        return "telegram_invalid_token"
    if status == 413 or "too large" in d:
        return "telegram_bot_file_too_large"
    if "chat not found" in d:
        return "telegram_chat_not_found"
    if status == 403 or "not enough rights" in d or "administrator" in d or "not a member" in d:
        return "telegram_bot_not_admin"
    return "telegram_rejected"


class TelegramBotAdapter(PlatformAdapter):
    platform: ClassVar[Platform] = Platform.TELEGRAM
    supported_formats: ClassVar[tuple[PostFormat, ...]] = (PostFormat.VIDEO, PostFormat.POST)

    def __init__(
        self,
        token: str,
        channel: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = API_BASE,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._channel = channel
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._base = f"{base_url}/bot{token}"
        self._max_retries = max_retries
        self._sleep = sleep

    @property
    def supports_native_scheduling(self) -> bool:
        return False

    # --- HTTP ----------------------------------------------------------------------

    def _call(
        self,
        method: str,
        *,
        data: dict[str, Any] | None = None,
        file: tuple[str, Path] | None = None,
    ) -> Any:
        """Call a Bot API method with retries.

        Retried: 429 (after ``retry_after``), 5xx, and failures to connect. A read timeout
        during an upload is NOT retried: Telegram may already have posted the video, and a
        retry would publish it twice.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._send(method, data, file)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                if attempt > self._max_retries:
                    raise PublishError("network_error", retryable=True) from exc
                self._sleep(self._backoff(attempt))
                continue
            except httpx.TimeoutException as exc:
                if file is None and attempt <= self._max_retries:
                    self._sleep(self._backoff(attempt))
                    continue
                raise PublishError("telegram_timeout_uncertain") from exc
            except httpx.TransportError as exc:
                raise PublishError("network_error", retryable=True) from exc

            body = self._json(resp)
            if body.get("ok") is True:
                return body.get("result")

            status = resp.status_code
            description = str(body.get("description", ""))
            if status == 429:
                retry_after = int(body.get("parameters", {}).get("retry_after", 1))
                if attempt > self._max_retries:
                    raise PublishError(
                        "telegram_rate_limited", retryable=True, retry_after=retry_after
                    )
                self._sleep(retry_after)
                continue
            if status >= 500:
                if attempt > self._max_retries:
                    raise PublishError("telegram_unavailable", retryable=True, status=status)
                self._sleep(self._backoff(attempt))
                continue
            raise PublishError(_classify(status, description), status=status)

    def _send(
        self, method: str, data: dict[str, Any] | None, file: tuple[str, Path] | None
    ) -> httpx.Response:
        url = f"{self._base}/{method}"
        if file is None:
            return self._client.post(url, data=data)
        field, path = file
        with path.open("rb") as fh:
            return self._client.post(url, data=data, files={field: (path.name, fh, "video/mp4")})

    @staticmethod
    def _json(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            # Proxies and outages return HTML; treat it by status code only.
            return {"ok": False, "description": ""}
        return body

    @staticmethod
    def _backoff(attempt: int) -> float:
        return float(min(2 ** (attempt - 1), 30))

    # --- PlatformAdapter -----------------------------------------------------------

    def authenticate(self) -> None:
        """Check the token and that the bot may post in the channel."""
        me = self._call("getMe")
        member = self._call("getChatMember", data={"chat_id": self._channel, "user_id": me["id"]})
        status = member.get("status")
        if status == "creator":
            return
        if status != "administrator" or not member.get("can_post_messages", False):
            raise PublishError("telegram_bot_not_admin")

    def validate(self, video: Video) -> ValidationResult:
        errors: list[str] = []
        path = video.path
        if not path.is_file():
            return ValidationResult(ok=False, errors=["video_not_found"])
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            errors.append("telegram_unsupported_format")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            errors.append("telegram_bot_file_too_large")
        return ValidationResult(ok=not errors, errors=errors)

    def publish(self, publication: Publication) -> PublishResult:
        video = publication.video
        check = self.validate(video)
        if not check.ok:
            code = check.errors[0]
            params: dict[str, Any] = {}
            if code == "telegram_bot_file_too_large":
                params = {
                    "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
                    "size_mb": round(video.path.stat().st_size / (1024 * 1024), 1),
                    "hint": "switch_to_account",
                }
            raise PublishError(code, **params)

        caption = build_caption(publication.text)
        if len(caption) > MAX_CAPTION:
            raise PublishError("caption_too_long", max=MAX_CAPTION, length=len(caption))

        data: dict[str, Any] = {"chat_id": self._channel, "supports_streaming": "true"}
        if caption:
            data["caption"] = caption
        if video.duration_seconds is not None:
            data["duration"] = str(round(video.duration_seconds))
        if video.width is not None and video.height is not None:
            data["width"] = str(video.width)
            data["height"] = str(video.height)

        message = self._call("sendVideo", data=data, file=("video", video.path))
        message_id = int(message["message_id"])
        return PublishResult(external_id=str(message_id), url=post_url(self._channel, message_id))

    def schedule(self, publication: Publication, at: datetime) -> PublishResult:
        raise PublishError("telegram_bot_no_native_scheduling")
