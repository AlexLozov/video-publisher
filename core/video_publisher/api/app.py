"""FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from video_publisher import __version__
from video_publisher.domain import Platform
from video_publisher.platforms import PlatformRegistry
from video_publisher.platforms import registry as default_registry


class HealthResponse(BaseModel):
    status: str
    version: str


class PlatformInfo(BaseModel):
    id: Platform
    name: str
    available: bool


PLATFORM_NAMES: dict[Platform, str] = {
    Platform.YOUTUBE: "YouTube / Shorts",
    Platform.TIKTOK: "TikTok",
    Platform.INSTAGRAM: "Instagram",
    Platform.FACEBOOK: "Facebook Page",
    Platform.TELEGRAM: "Telegram",
}


def create_app(
    ui_dist_dir: Path | None = None,
    platform_registry: PlatformRegistry | None = None,
) -> FastAPI:
    """Build the API app. If ``ui_dist_dir`` exists, the built UI is served at ``/``."""
    reg = platform_registry or default_registry
    app = FastAPI(title="Video Publisher", version=__version__)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.get("/api/platforms", response_model=list[PlatformInfo])
    def platforms() -> list[PlatformInfo]:
        return [
            PlatformInfo(id=p, name=PLATFORM_NAMES[p], available=reg.is_registered(p))
            for p in Platform
        ]

    if ui_dist_dir is not None and ui_dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=ui_dist_dir, html=True), name="ui")

    return app
