"""Talking to Telegram as the user's own account (MTProto through Telethon).

Shared by the login flow and the account publishing adapter:

* ``ClientFactory`` builds a client from a Telethon ``StringSession`` string. The session
  only ever lives in memory and in the OS credential store, never in ``.session`` files.
* ``run`` executes the async Telethon code from our synchronous services (API worker
  threads and the publishing pool have no running event loop).
* ``common_error`` maps errors that can happen in any call to our error codes.

Tests replace the factory with a fake client; nothing here is reached without network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Protocol, TypeVar, cast

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from video_publisher import __version__
from video_publisher.platforms.errors import PublishError

T = TypeVar("T")


class SessionLike(Protocol):
    def save(self) -> str: ...


class AccountClient(Protocol):
    """The subset of ``telethon.TelegramClient`` the app uses."""

    session: SessionLike

    async def connect(self) -> None: ...

    def disconnect(self) -> Awaitable[None] | None: ...

    async def is_user_authorized(self) -> bool: ...

    async def send_code_request(self, phone: str) -> Any: ...

    async def sign_in(
        self,
        phone: str | None = None,
        code: str | None = None,
        *,
        password: str | None = None,
        phone_code_hash: str | None = None,
    ) -> Any: ...

    async def log_out(self) -> bool: ...

    async def get_input_entity(self, peer: Any) -> Any: ...

    async def get_dialogs(self, limit: int | None = None) -> Any: ...

    async def upload_file(
        self, file: Any, *, file_size: int | None = None, file_name: str | None = None
    ) -> Any: ...

    async def send_file(self, entity: Any, file: Any, **kwargs: Any) -> Any: ...

    async def __call__(self, request: Any) -> Any:
        """Send a raw MTProto request (e.g. ``DeleteScheduledMessagesRequest``)."""


ClientFactory = Callable[[str, int, str], AccountClient]


def default_client_factory(session: str, api_id: int, api_hash: str) -> AccountClient:
    client = TelegramClient(
        StringSession(session),
        api_id,
        api_hash,
        device_model="Video Publisher",
        app_version=__version__,
        # Short waits are slept through by Telethon; longer ones are reported to the user.
        flood_sleep_threshold=30,
    )
    return cast(AccountClient, client)


def run(coro: Coroutine[Any, Any, T]) -> T:
    """Run Telethon code from a thread without an event loop."""
    return asyncio.run(coro)


async def close(client: AccountClient) -> None:
    """Disconnect, ignoring errors (the connection may already be gone)."""
    try:
        result = client.disconnect()
        if result is not None:
            await result
    except Exception:  # pragma: no cover - best effort
        pass


# Errors that mean the stored login no longer works (logged out elsewhere, banned...).
SESSION_INVALID_ERRORS: tuple[type[BaseException], ...] = (
    errors.AuthKeyUnregisteredError,
    errors.AuthKeyDuplicatedError,
    errors.SessionRevokedError,
    errors.SessionExpiredError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
)

NETWORK_ERRORS: tuple[type[BaseException], ...] = (ConnectionError, OSError, TimeoutError)


def common_error(exc: BaseException) -> PublishError | None:
    """Our error for failures that are the same in every call, or None."""
    if isinstance(exc, errors.FloodWaitError):
        return PublishError("telegram_flood_wait", retryable=True, seconds=int(exc.seconds))
    if isinstance(exc, (errors.ApiIdInvalidError, errors.ApiIdPublishedFloodError)):
        return PublishError("telegram_api_keys_invalid")
    if isinstance(exc, SESSION_INVALID_ERRORS):
        return PublishError("telegram_session_expired")
    if isinstance(exc, NETWORK_ERRORS):
        return PublishError("network_error", retryable=True)
    return None
