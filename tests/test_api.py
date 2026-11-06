from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fakes import FakeTelegram
from video_publisher import __version__
from video_publisher.api import create_app
from video_publisher.api.app import create_app_from_settings
from video_publisher.platforms import PlatformRegistry

OpenClient = Callable[[FastAPI], TestClient]


@pytest.fixture
def open_client() -> Iterator[OpenClient]:
    """Start apps with their lifespan, so they release their database on exit."""
    clients: list[TestClient] = []

    def start(app: FastAPI) -> TestClient:
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield start
    for client in clients:
        client.__exit__(None, None, None)


def default_app(**kwargs: object) -> FastAPI:
    kwargs.setdefault("platform_registry", PlatformRegistry())
    return create_app(**kwargs)  # type: ignore[arg-type]


def test_health(open_client: OpenClient) -> None:
    resp = open_client(default_app()).get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_serves_built_ui_when_dist_exists(open_client: OpenClient, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>ui</h1>", encoding="utf-8")
    client = open_client(default_app(ui_dist_dir=tmp_path))
    assert client.get("/").text == "<h1>ui</h1>"
    assert client.get("/api/health").status_code == 200


def test_missing_ui_dir_is_not_mounted(open_client: OpenClient, tmp_path: Path) -> None:
    client = open_client(default_app(ui_dist_dir=tmp_path / "nope"))
    assert client.get("/").status_code == 404


def test_factory_reads_ui_dir_from_env(
    open_client: OpenClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("from env", encoding="utf-8")
    monkeypatch.setenv("VP_UI_DIST_DIR", str(tmp_path))
    assert open_client(create_app_from_settings()).get("/").text == "from env"


def test_unknown_api_route_returns_error_code(open_client: OpenClient) -> None:
    resp = open_client(default_app()).get("/api/nope")
    assert resp.status_code == 404
    assert resp.json() == {"code": "not_found", "params": {}}


def test_platforms_lists_all_enum_members(open_client: OpenClient) -> None:
    resp = open_client(default_app()).get("/api/platforms")
    assert resp.status_code == 200
    assert resp.json() == [
        {"id": p, "available": False}
        for p in ["youtube", "tiktok", "instagram", "facebook", "telegram"]
    ]


def test_platforms_marks_registered_adapters_available(open_client: OpenClient) -> None:
    reg = PlatformRegistry()
    reg.register(FakeTelegram)
    data = open_client(default_app(platform_registry=reg)).get("/api/platforms").json()
    assert {p["id"]: p["available"] for p in data}["telegram"] is True
    assert sum(p["available"] for p in data) == 1


def test_platforms_response_has_no_display_text(open_client: OpenClient) -> None:
    """User-facing names are translated in the UI, so the API must not send them."""
    data = open_client(default_app()).get("/api/platforms").json()
    assert all(set(p) == {"id", "available"} for p in data)


def test_shutdown_releases_database(tmp_path: Path) -> None:
    app = default_app()
    with TestClient(app) as client:
        assert client.get("/api/settings").status_code == 200
    # After shutdown the pool is disposed: no checked-out or idle connections remain.
    engine = app.state.settings_service.session_factory.kw["bind"]
    assert engine.pool.checkedout() == 0
    assert app.state.publishing_service._executor._shutdown is True
