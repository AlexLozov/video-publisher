"""Logging in to the user's own Telegram account (needed for "my account" publishing).

States: ``logged_out`` → ``code_sent`` → (``password_needed`` if 2FA is on) → ``logged_in``.

* The login is done once on the Settings screen. The finished Telethon ``StringSession``
  is stored in the secret store (Windows Credential Manager) as ``telegram_session``.
* While the login is in progress (code sent, password asked) the half-finished session
  and ``phone_code_hash`` are kept in memory only. Restarting the app cancels the login.
* The phone number, code and password are never stored or logged.
"""

from __future__ import annotations

import contextlib
import re
import threading
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel
from telethon import errors

from video_publisher.auth.secret_store import SecretName
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.telegram_mtproto import (
    AccountClient,
    ClientFactory,
    close,
    common_error,
    default_client_factory,
    run,
)
from video_publisher.user_settings import SettingsService

_PHONE_RE = re.compile(r"^\+?\d{7,15}$")
_CODE_RE = re.compile(r"^\d{4,8}$")
MAX_PASSWORD = 256


class LoginState(StrEnum):
    LOGGED_OUT = "logged_out"
    CODE_SENT = "code_sent"
    PASSWORD_NEEDED = "password_needed"
    LOGGED_IN = "logged_in"


class LoginStatus(BaseModel):
    state: LoginState
    # Masked phone while a login is in progress, e.g. "+7••••••4567".
    phone: str | None = None


@dataclass
class _Pending:
    phone: str
    phone_code_hash: str
    session: str
    password_needed: bool = False


def normalize_phone(raw: str) -> str:
    phone = re.sub(r"[\s()\-.]", "", raw)
    if not _PHONE_RE.match(phone):
        raise PublishError("telegram_phone_invalid")
    return phone if phone.startswith("+") else f"+{phone}"


def mask_phone(phone: str) -> str:
    return phone[:2] + "•" * max(len(phone) - 6, 0) + phone[-4:]


def _login_error(exc: Exception) -> PublishError:
    if isinstance(exc, PublishError):
        return exc
    if isinstance(exc, errors.PhoneNumberInvalidError):
        return PublishError("telegram_phone_invalid")
    if isinstance(exc, errors.PhoneNumberBannedError):
        return PublishError("telegram_phone_banned")
    if isinstance(exc, errors.PhoneNumberUnoccupiedError):
        return PublishError("telegram_phone_not_registered")
    if isinstance(exc, (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError)):
        return PublishError("telegram_code_invalid")
    if isinstance(exc, (errors.PhoneCodeExpiredError, errors.PhoneHashExpiredError)):
        return PublishError("telegram_code_expired")
    if isinstance(exc, (errors.PasswordHashInvalidError, errors.PasswordEmptyError)):
        return PublishError("telegram_password_invalid")
    if isinstance(exc, (errors.PhoneNumberFloodError, errors.PhonePasswordFloodError)):
        return PublishError("telegram_too_many_attempts")
    mapped = common_error(exc)
    if mapped is not None:
        return mapped
    return PublishError("telegram_login_failed")


# After these the code cannot be used any more: the user starts over with the phone step.
_RESTART_CODES = frozenset(
    {"telegram_code_expired", "telegram_phone_not_registered", "telegram_phone_banned"}
)


class TelegramLoginService:
    def __init__(
        self, settings: SettingsService, client_factory: ClientFactory = default_client_factory
    ) -> None:
        self._settings = settings
        self._factory = client_factory
        self._lock = threading.Lock()
        self._pending: _Pending | None = None

    # --- state ------------------------------------------------------------------------

    def status(self) -> LoginStatus:
        if self._settings.get_secret(SecretName.TELEGRAM_SESSION):
            return LoginStatus(state=LoginState.LOGGED_IN)
        pending = self._pending
        if pending is None:
            return LoginStatus(state=LoginState.LOGGED_OUT)
        state = LoginState.PASSWORD_NEEDED if pending.password_needed else LoginState.CODE_SENT
        return LoginStatus(state=state, phone=mask_phone(pending.phone))

    def _credentials(self) -> tuple[int, str]:
        api_id = self._settings.get().telegram.api_id
        api_hash = self._settings.get_secret(SecretName.TELEGRAM_API_HASH)
        missing = [n for n, v in (("api_id", api_id), ("api_hash", api_hash)) if not v]
        if missing:
            raise PublishError("telegram_api_keys_missing", missing=missing)
        assert api_id is not None and api_hash is not None
        return api_id, api_hash

    def _client(self, session: str) -> AccountClient:
        api_id, api_hash = self._credentials()
        return self._factory(session, api_id, api_hash)

    # --- steps ------------------------------------------------------------------------

    def send_code(self, raw_phone: str) -> LoginStatus:
        with self._lock:
            if self._settings.get_secret(SecretName.TELEGRAM_SESSION):
                raise PublishError("telegram_already_logged_in")
            phone = normalize_phone(raw_phone)
            client = self._client("")

            async def go() -> tuple[str, str]:
                try:
                    await client.connect()
                    sent = await client.send_code_request(phone)
                    return str(sent.phone_code_hash), client.session.save()
                finally:
                    await close(client)

            try:
                code_hash, session = run(go())
            except Exception as exc:
                raise _login_error(exc) from exc
            self._pending = _Pending(phone=phone, phone_code_hash=code_hash, session=session)
            return self.status()

    def sign_in(self, raw_code: str) -> LoginStatus:
        with self._lock:
            pending = self._pending
            if pending is None or pending.password_needed:
                raise PublishError("telegram_login_not_started")
            code = re.sub(r"\s", "", raw_code)
            if not _CODE_RE.match(code):
                raise PublishError("telegram_code_invalid")
            client = self._client(pending.session)

            async def go() -> tuple[bool, str]:
                try:
                    await client.connect()
                    try:
                        await client.sign_in(
                            pending.phone, code, phone_code_hash=pending.phone_code_hash
                        )
                    except errors.SessionPasswordNeededError:
                        return True, client.session.save()
                    return False, client.session.save()
                finally:
                    await close(client)

            try:
                password_needed, session = run(go())
            except Exception as exc:
                err = _login_error(exc)
                if err.code in _RESTART_CODES:
                    self._pending = None
                raise err from exc
            if password_needed:
                pending.password_needed = True
                pending.session = session
            else:
                self._finish(session)
            return self.status()

    def check_password(self, password: str) -> LoginStatus:
        with self._lock:
            pending = self._pending
            if pending is None or not pending.password_needed:
                raise PublishError("telegram_login_not_started")
            if not password or len(password) > MAX_PASSWORD:
                raise PublishError("telegram_password_invalid")
            client = self._client(pending.session)

            async def go() -> str:
                try:
                    await client.connect()
                    await client.sign_in(password=password)
                    return client.session.save()
                finally:
                    await close(client)

            try:
                session = run(go())
            except Exception as exc:
                raise _login_error(exc) from exc
            self._finish(session)
            return self.status()

    def _finish(self, session: str) -> None:
        self._settings.set_secret(SecretName.TELEGRAM_SESSION, session)
        self._pending = None

    def cancel(self) -> LoginStatus:
        with self._lock:
            self._pending = None
            return self.status()

    def logout(self) -> LoginStatus:
        """End the session on Telegram's side (best effort) and forget it locally.

        The local session is removed even when Telegram cannot be reached, so the user
        is never stuck "logged in". Posts already scheduled in the channel stay there.
        """
        with self._lock:
            self._pending = None
            session = self._settings.get_secret(SecretName.TELEGRAM_SESSION)
            if session:
                with contextlib.suppress(Exception):  # remote logout is best effort
                    client = self._client(session)

                    async def go() -> None:
                        try:
                            await client.connect()
                            await client.log_out()
                        finally:
                            await close(client)

                    run(go())
            self._settings.delete_secret(SecretName.TELEGRAM_SESSION)
            return self.status()
