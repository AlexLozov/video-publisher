"""Logging in to the user's own Telegram account (Settings → Telegram → "My account").

Every endpoint returns the login state: ``{"state": ..., "phone": "+7••••••4567"|null}``.
Errors come as codes, e.g. ``telegram_code_invalid`` or ``telegram_flood_wait`` with
``{"seconds": 120}``. The phone number, code and password are never stored or logged.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from video_publisher.api.errors import ApiError
from video_publisher.auth.secret_store import SecretStoreError
from video_publisher.auth.telegram_login import MAX_PASSWORD, LoginStatus, TelegramLoginService
from video_publisher.platforms.errors import PublishError


def get_service(request: Request) -> TelegramLoginService:
    service: TelegramLoginService = request.app.state.telegram_login_service
    return service


Service = Annotated[TelegramLoginService, Depends(get_service)]

router = APIRouter(prefix="/api/telegram/account")

_STATUS_BY_CODE = {
    "telegram_flood_wait": 429,
    "telegram_too_many_attempts": 429,
    "network_error": 502,
    "telegram_already_logged_in": 409,
    "telegram_login_not_started": 409,
}


class PhoneBody(BaseModel):
    phone: str = Field(min_length=1, max_length=32)


class CodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=16)


class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)


def _call(action: Callable[[], LoginStatus]) -> LoginStatus:
    try:
        return action()
    except PublishError as exc:
        status = _STATUS_BY_CODE.get(exc.code, 422)
        raise ApiError(exc.code, status_code=status, **exc.params) from exc
    except SecretStoreError as exc:
        raise ApiError("secret_store_unavailable", status_code=503) from exc


@router.get("", response_model=LoginStatus)
def login_status(service: Service) -> LoginStatus:
    return _call(service.status)


@router.post("/send-code", response_model=LoginStatus)
def send_code(body: PhoneBody, service: Service) -> LoginStatus:
    return _call(lambda: service.send_code(body.phone))


@router.post("/sign-in", response_model=LoginStatus)
def sign_in(body: CodeBody, service: Service) -> LoginStatus:
    return _call(lambda: service.sign_in(body.code))


@router.post("/password", response_model=LoginStatus)
def check_password(body: PasswordBody, service: Service) -> LoginStatus:
    return _call(lambda: service.check_password(body.password))


@router.post("/cancel", response_model=LoginStatus)
def cancel(service: Service) -> LoginStatus:
    return _call(service.cancel)


@router.post("/logout", response_model=LoginStatus)
def logout(service: Service) -> LoginStatus:
    return _call(service.logout)
