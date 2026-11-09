"""Settings and secrets endpoints.

Secret values are write-only: the API reports only whether each secret is set.
"""

from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from video_publisher.api.errors import ApiError
from video_publisher.auth.secret_store import SecretName, SecretStoreError
from video_publisher.user_settings import AppSettings, SettingsPatch, SettingsService
from video_publisher.user_settings.service import InvalidSecretError


class WritableSecret(StrEnum):
    """Secrets the user may set by hand (sessions and page tokens come from login flows)."""

    TELEGRAM_BOT_TOKEN = SecretName.TELEGRAM_BOT_TOKEN.value
    TELEGRAM_API_HASH = SecretName.TELEGRAM_API_HASH.value
    FACEBOOK_APP_SECRET = SecretName.FACEBOOK_APP_SECRET.value
    YOUTUBE_CLIENT_SECRET = SecretName.YOUTUBE_CLIENT_SECRET.value


class SettingsResponse(AppSettings):
    secrets: dict[SecretName, bool]


class SecretValue(BaseModel):
    value: str = Field(min_length=1, max_length=2000)


def get_service(request: Request) -> SettingsService:
    service: SettingsService = request.app.state.settings_service
    return service


Service = Annotated[SettingsService, Depends(get_service)]

router = APIRouter(prefix="/api")


def _response(service: SettingsService, settings: AppSettings) -> SettingsResponse:
    try:
        secrets = service.secret_status()
    except SecretStoreError as exc:
        raise ApiError("secret_store_unavailable", status_code=503) from exc
    return SettingsResponse(**settings.model_dump(), secrets=secrets)


@router.get("/settings", response_model=SettingsResponse)
def read_settings(service: Service) -> SettingsResponse:
    return _response(service, service.get())


@router.patch("/settings", response_model=SettingsResponse)
def update_settings(patch: SettingsPatch, service: Service) -> SettingsResponse:
    return _response(service, service.update(patch))


@router.put("/secrets/{name}", status_code=204)
def set_secret(name: WritableSecret, body: SecretValue, service: Service) -> Response:
    try:
        service.set_secret(SecretName(name.value), body.value)
    except InvalidSecretError as exc:
        raise ApiError(exc.code, status_code=422) from exc
    except SecretStoreError as exc:
        raise ApiError("secret_store_unavailable", status_code=503) from exc
    return Response(status_code=204)


@router.delete("/secrets/{name}", status_code=204)
def delete_secret(name: SecretName, service: Service) -> Response:
    try:
        service.delete_secret(name)
    except SecretStoreError as exc:
        raise ApiError("secret_store_unavailable", status_code=503) from exc
    return Response(status_code=204)
