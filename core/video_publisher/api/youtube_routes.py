"""Logging in with Google for YouTube (Settings → YouTube).

Every endpoint returns the login status (see ``YouTubeLoginStatus``); tokens are
never part of a response. Errors come as codes, e.g. ``youtube_app_not_configured``.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from video_publisher.api.errors import ApiError
from video_publisher.auth.secret_store import SecretStoreError
from video_publisher.auth.youtube_login import YouTubeLoginService, YouTubeLoginStatus
from video_publisher.platforms.errors import PublishError


def get_service(request: Request) -> YouTubeLoginService:
    service: YouTubeLoginService = request.app.state.youtube_login_service
    return service


Service = Annotated[YouTubeLoginService, Depends(get_service)]

router = APIRouter(prefix="/api/youtube/account")

_STATUS_BY_CODE = {
    "youtube_already_logged_in": 409,
    "youtube_port_busy": 409,
    "network_error": 502,
}


def _call(action: Callable[[], YouTubeLoginStatus]) -> YouTubeLoginStatus:
    try:
        return action()
    except PublishError as exc:
        raise ApiError(
            exc.code, status_code=_STATUS_BY_CODE.get(exc.code, 422), **exc.params
        ) from exc
    except SecretStoreError as exc:
        raise ApiError("secret_store_unavailable", status_code=503) from exc


@router.get("", response_model=YouTubeLoginStatus)
def login_status(service: Service) -> YouTubeLoginStatus:
    return _call(service.status)


@router.post("/verify", response_model=YouTubeLoginStatus)
def verify(service: Service) -> YouTubeLoginStatus:
    """The status after checking with Google that the stored login still works."""
    return _call(service.verify)


@router.post("/login", response_model=YouTubeLoginStatus)
def login(service: Service) -> YouTubeLoginStatus:
    return _call(service.start)


@router.post("/cancel", response_model=YouTubeLoginStatus)
def cancel(service: Service) -> YouTubeLoginStatus:
    return _call(service.cancel)


@router.post("/logout", response_model=YouTubeLoginStatus)
def logout(service: Service) -> YouTubeLoginStatus:
    return _call(service.logout)
