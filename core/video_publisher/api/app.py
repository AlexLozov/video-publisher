"""FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from video_publisher import __version__
from video_publisher.api.errors import install_error_handlers
from video_publisher.api.settings_routes import router as settings_router
from video_publisher.config import get_settings
from video_publisher.domain import Platform
from video_publisher.platforms import PlatformRegistry
from video_publisher.platforms import registry as default_registry
from video_publisher.user_settings import SettingsService, build_settings_service


class HealthResponse(BaseModel):
    status: str
    version: str


class PlatformInfo(BaseModel):
    """A platform as the UI sees it. Display names are translated on the client by ``id``."""

    id: Platform
    available: bool


def create_app(
    ui_dist_dir: Path | None = None,
    platform_registry: PlatformRegistry | None = None,
    settings_service: SettingsService | None = None,
) -> FastAPI:
    """Build the API app. If ``ui_dist_dir`` exists, the built UI is served at ``/``.

    Without an explicit ``settings_service`` the database in ``VP_DATA_DIR`` is migrated
    and used.
    """
    reg = default_registry if platform_registry is None else platform_registry
    app = FastAPI(title="Video Publisher", version=__version__)
    install_error_handlers(app)
    app.state.settings_service = (
        build_settings_service(get_settings()) if settings_service is None else settings_service
    )
    app.include_router(settings_router)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.get("/api/platforms", response_model=list[PlatformInfo])
    def platforms() -> list[PlatformInfo]:
        return [PlatformInfo(id=p, available=reg.is_registered(p)) for p in Platform]

    if ui_dist_dir is not None and ui_dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=ui_dist_dir, html=True), name="ui")

    return app


def create_app_from_settings() -> FastAPI:
    """Factory for ``uvicorn --factory``: takes the UI directory from settings (VP_UI_DIST_DIR)."""
    return create_app(ui_dist_dir=get_settings().ui_dist_dir)
