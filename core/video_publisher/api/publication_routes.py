"""Publishing endpoints: start a publication and follow its status."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel

from video_publisher.api.errors import ApiError
from video_publisher.media.probe import probe
from video_publisher.platforms.errors import PublishError
from video_publisher.publishing import (
    BatchPublishRequest,
    PublicationView,
    PublishingService,
    PublishRequest,
)


def get_service(request: Request) -> PublishingService:
    service: PublishingService = request.app.state.publishing_service
    return service


Service = Annotated[PublishingService, Depends(get_service)]

router = APIRouter(prefix="/api/publications")

# The history screen loads everything it shows (and exports to CSV) in one request.
MAX_LIST = 1000


@router.post("", status_code=202, response_model=PublicationView)
def start_publication(body: PublishRequest, service: Service) -> PublicationView:
    """Validate and start publishing in the background. Poll GET /{id} for the result."""
    try:
        return service.start(body)
    except PublishError as exc:
        raise ApiError(exc.code, status_code=422, **exc.params) from exc


@router.post("/batch", status_code=202, response_model=list[PublicationView])
def start_batch(body: BatchPublishRequest, service: Service) -> list[PublicationView]:
    """One video to several networks. Nothing starts unless every network accepts it."""
    try:
        return service.start_batch(body)
    except PublishError as exc:
        raise ApiError(exc.code, status_code=422, **exc.params) from exc


class VideoInfoView(BaseModel):
    """What the upload screen shows about the chosen file (unknown values are null)."""

    duration_seconds: float | None
    width: int | None
    height: int | None
    # Vertical or square and up to 3 minutes: YouTube shows it as a Short.
    youtube_short: bool | None


@router.get("/video-info", response_model=VideoInfoView)
def video_info(path: Annotated[str, Query(min_length=1, max_length=4096)]) -> VideoInfoView:
    """Duration and frame size of the chosen file (read locally, nothing is uploaded)."""
    file = Path(path.strip())
    if not file.is_file():
        raise ApiError("video_not_found", status_code=404)
    info = probe(file)
    if info is None:
        return VideoInfoView(duration_seconds=None, width=None, height=None, youtube_short=None)
    return VideoInfoView(
        duration_seconds=info.duration_seconds,
        width=info.width,
        height=info.height,
        youtube_short=info.is_youtube_short,
    )


@router.get("", response_model=list[PublicationView])
def list_publications(
    service: Service, limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = 20
) -> list[PublicationView]:
    return service.recent(limit)


class ClearResult(BaseModel):
    deleted: int


@router.delete("", response_model=ClearResult)
def clear_history(service: Service) -> ClearResult:
    """Remove finished records; scheduled and running publications stay."""
    return ClearResult(deleted=service.clear())


@router.delete("/{pub_id}", status_code=204)
def delete_publication(pub_id: str, service: Service) -> Response:
    """Remove one record; a scheduled post is withdrawn from the platform first."""
    try:
        found = service.delete(pub_id)
    except PublishError as exc:
        status = 409 if exc.code == "publication_in_progress" else 422
        raise ApiError(exc.code, status_code=status, **exc.params) from exc
    if not found:
        raise ApiError("publication_not_found", status_code=404)
    return Response(status_code=204)


@router.get("/{pub_id}", response_model=PublicationView)
def get_publication(pub_id: str, service: Service) -> PublicationView:
    view = service.get(pub_id)
    if view is None:
        raise ApiError("publication_not_found", status_code=404)
    return view
