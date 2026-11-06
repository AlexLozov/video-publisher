"""Publishing endpoints: start a publication and follow its status."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from video_publisher.api.errors import ApiError
from video_publisher.platforms.errors import PublishError
from video_publisher.publishing import PublicationView, PublishingService, PublishRequest


def get_service(request: Request) -> PublishingService:
    service: PublishingService = request.app.state.publishing_service
    return service


Service = Annotated[PublishingService, Depends(get_service)]

router = APIRouter(prefix="/api/publications")


@router.post("", status_code=202, response_model=PublicationView)
def start_publication(body: PublishRequest, service: Service) -> PublicationView:
    """Validate and start publishing in the background. Poll GET /{id} for the result."""
    try:
        return service.start(body)
    except PublishError as exc:
        raise ApiError(exc.code, status_code=422, **exc.params) from exc


@router.get("", response_model=list[PublicationView])
def list_publications(
    service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[PublicationView]:
    return service.recent(limit)


@router.get("/{pub_id}", response_model=PublicationView)
def get_publication(pub_id: str, service: Service) -> PublicationView:
    view = service.get(pub_id)
    if view is None:
        raise ApiError("publication_not_found", status_code=404)
    return view
