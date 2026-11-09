"""Logging in with Facebook (Settings → Facebook).

Every endpoint returns the login status (see ``FacebookLoginStatus``); page tokens are
never part of a response. Errors come as codes, e.g. ``facebook_app_not_configured``.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from video_publisher.api.errors import ApiError
from video_publisher.auth.facebook_login import FacebookLoginService, FacebookLoginStatus
from video_publisher.auth.secret_store import SecretStoreError
from video_publisher.platforms.errors import PublishError


def get_service(request: Request) -> FacebookLoginService:
    service: FacebookLoginService = request.app.state.facebook_login_service
    return service


Service = Annotated[FacebookLoginService, Depends(get_service)]

router = APIRouter(prefix="/api/facebook/account")

_STATUS_BY_CODE = {
    "facebook_already_logged_in": 409,
    "facebook_page_not_offered": 409,
    "facebook_port_busy": 409,
    "network_error": 502,
}


class PageChoice(BaseModel):
    page_id: str = Field(min_length=1, max_length=64)


def _call(action: Callable[[], FacebookLoginStatus]) -> FacebookLoginStatus:
    try:
        return action()
    except PublishError as exc:
        raise ApiError(
            exc.code, status_code=_STATUS_BY_CODE.get(exc.code, 422), **exc.params
        ) from exc
    except SecretStoreError as exc:
        raise ApiError("secret_store_unavailable", status_code=503) from exc


@router.get("", response_model=FacebookLoginStatus)
def login_status(service: Service) -> FacebookLoginStatus:
    return _call(service.status)


@router.post("/login", response_model=FacebookLoginStatus)
def login(service: Service) -> FacebookLoginStatus:
    return _call(service.start)


@router.post("/select-page", response_model=FacebookLoginStatus)
def select_page(body: PageChoice, service: Service) -> FacebookLoginStatus:
    return _call(lambda: service.select_page(body.page_id))


@router.post("/cancel", response_model=FacebookLoginStatus)
def cancel(service: Service) -> FacebookLoginStatus:
    return _call(service.cancel)


@router.post("/logout", response_model=FacebookLoginStatus)
def logout(service: Service) -> FacebookLoginStatus:
    return _call(service.logout)
