"""Telegram channel publishing from the user's own account (MTProto through Telethon).

Compared with the bot: videos up to 2 GB, and scheduled posts are kept by Telegram itself
(``schedule_date``), so they go out even when the computer is off.

Uploading happens in two steps so that a broken connection is reported honestly:
1. ``upload_file`` sends the file parts. Nothing is visible in the channel yet, so a
   network failure here is a plain, retryable ``network_error``.
2. ``send_file`` posts the uploaded file. If the connection drops now, the post may or
   may not exist, so we report ``telegram_timeout_uncertain`` and never retry by ourselves.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from telethon import errors
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo

from video_publisher.domain import Platform, PostFormat, Publication, Video
from video_publisher.platforms.base import PlatformAdapter, PublishResult, ValidationResult
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.scheduling import check_schedule_time
from video_publisher.platforms.telegram_bot import (
    MAX_CAPTION,
    SUPPORTED_EXTENSIONS,
    build_caption,
    post_url,
)
from video_publisher.platforms.telegram_mtproto import (
    NETWORK_ERRORS,
    AccountClient,
    ClientFactory,
    close,
    common_error,
    default_client_factory,
    run,
)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
# Telegram keeps scheduled messages for at most a year.
MAX_SCHEDULE_AHEAD = timedelta(days=365)

_NO_RIGHTS = (
    errors.ChatAdminRequiredError,
    errors.ChatWriteForbiddenError,
    errors.ChatSendMediaForbiddenError,
    errors.UserBannedInChannelError,
)
_NOT_FOUND = (
    errors.ChannelPrivateError,
    errors.ChannelInvalidError,
    errors.PeerIdInvalidError,
    errors.UsernameNotOccupiedError,
    errors.UsernameInvalidError,
)


def _publish_error(exc: Exception) -> PublishError:
    """Map an error from resolving the channel or posting to our error code."""
    if isinstance(exc, PublishError):
        return exc
    if isinstance(exc, _NO_RIGHTS):
        return PublishError("telegram_account_not_admin")
    if isinstance(exc, _NOT_FOUND):
        return PublishError("telegram_chat_not_found")
    if isinstance(exc, errors.ScheduleDateTooLateError):
        return PublishError("schedule_too_far", max_days=MAX_SCHEDULE_AHEAD.days)
    if isinstance(exc, errors.ScheduleDateInvalidError):
        return PublishError("schedule_in_past", min_minutes=1)
    if isinstance(exc, errors.ScheduleTooMuchError):
        return PublishError("telegram_schedule_limit")
    if isinstance(exc, errors.MediaCaptionTooLongError):
        return PublishError("caption_too_long", max=MAX_CAPTION)
    mapped = common_error(exc)
    if mapped is not None:
        return mapped
    return PublishError("telegram_rejected")


def _peer(channel: str) -> str | int:
    return int(channel) if channel.startswith("-100") else channel


class TelegramAccountAdapter(PlatformAdapter):
    platform: ClassVar[Platform] = Platform.TELEGRAM
    supported_formats: ClassVar[tuple[PostFormat, ...]] = (PostFormat.VIDEO, PostFormat.POST)
    max_schedule_ahead: ClassVar[timedelta] = MAX_SCHEDULE_AHEAD

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session: str,
        channel: str,
        client_factory: ClientFactory = default_client_factory,
        on_session_invalid: Callable[[], None] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session = session
        self._channel = channel
        self._factory = client_factory
        self._on_session_invalid = on_session_invalid
        self._now = now

    @property
    def supports_native_scheduling(self) -> bool:
        return True

    # --- helpers --------------------------------------------------------------------

    def _client(self) -> AccountClient:
        return self._factory(self._session, self._api_id, self._api_hash)

    def _fail(self, err: PublishError) -> PublishError:
        if err.code == "telegram_session_expired" and self._on_session_invalid is not None:
            self._on_session_invalid()
        return err

    async def _connect(self, client: AccountClient) -> None:
        await client.connect()
        if not await client.is_user_authorized():
            raise PublishError("telegram_session_expired")

    async def _resolve(self, client: AccountClient) -> Any:
        """The channel as an input peer.

        A ``@username`` is looked up on the server. A numeric ``-100…`` id can only be
        resolved from the account's chat list, which a fresh session does not have yet.
        """
        peer = _peer(self._channel)
        try:
            return await client.get_input_entity(peer)
        except ValueError:
            if isinstance(peer, str):
                raise PublishError("telegram_chat_not_found") from None
        await client.get_dialogs()
        try:
            return await client.get_input_entity(peer)
        except ValueError:
            raise PublishError("telegram_chat_not_found") from None

    # --- PlatformAdapter -----------------------------------------------------------

    def authenticate(self) -> None:
        """Check that the stored login still works."""
        client = self._client()

        async def go() -> None:
            try:
                await self._connect(client)
            finally:
                await close(client)

        try:
            run(go())
        except Exception as exc:
            raise self._fail(_publish_error(exc)) from exc

    def validate(self, video: Video) -> ValidationResult:
        path = video.path
        if not path.is_file():
            return ValidationResult(ok=False, errors=["video_not_found"])
        errors_: list[str] = []
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            errors_.append("telegram_unsupported_format")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            errors_.append("telegram_account_file_too_large")
        return ValidationResult(ok=not errors_, errors=errors_)

    def publish(self, publication: Publication) -> PublishResult:
        return self._send(publication, None)

    def schedule(self, publication: Publication, at: datetime) -> PublishResult:
        check_schedule_time(at, self._now(), MAX_SCHEDULE_AHEAD)
        return self._send(publication, at)

    def _send(self, publication: Publication, at: datetime | None) -> PublishResult:
        video = publication.video
        check = self.validate(video)
        if not check.ok:
            code = check.errors[0]
            params: dict[str, Any] = {}
            if code == "telegram_account_file_too_large":
                params = {
                    "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
                    "size_mb": round(video.path.stat().st_size / (1024 * 1024), 1),
                }
            raise PublishError(code, **params)

        caption = build_caption(publication.text)
        if len(caption) > MAX_CAPTION:
            raise PublishError("caption_too_long", max=MAX_CAPTION, length=len(caption))

        size = video.path.stat().st_size
        attributes: list[Any] = [DocumentAttributeFilename(video.path.name)]
        if video.width is not None and video.height is not None:
            attributes.append(
                DocumentAttributeVideo(
                    duration=video.duration_seconds or 0,
                    w=video.width,
                    h=video.height,
                    supports_streaming=True,
                )
            )
        client = self._client()

        async def go() -> int:
            try:
                await self._connect(client)
                entity = await self._resolve(client)
                uploaded = await client.upload_file(
                    str(video.path), file_size=size, file_name=video.path.name
                )
                try:
                    message = await client.send_file(
                        entity,
                        uploaded,
                        caption=caption or None,
                        parse_mode=None,  # the caption is plain text, not Markdown
                        mime_type="video/mp4",
                        attributes=attributes,
                        supports_streaming=True,
                        schedule=at,
                    )
                except NETWORK_ERRORS as exc:
                    raise PublishError("telegram_timeout_uncertain") from exc
                return int(message.id)
            finally:
                await close(client)

        try:
            message_id = run(go())
        except Exception as exc:
            raise self._fail(_publish_error(exc)) from exc

        # A scheduled message has no public link until Telegram posts it.
        url = None if at is not None else post_url(self._channel, message_id)
        return PublishResult(external_id=str(message_id), url=url)
