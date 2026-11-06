from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_publisher import __version__
from video_publisher.api import create_app
from video_publisher.api.app import create_app_from_settings
from video_publisher.platforms import PlatformRegistry


def test_health() -> None:
    client = TestClient(create_app(platform_registry=PlatformRegistry()))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_serves_built_ui_when_dist_exists(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>ui</h1>", encoding="utf-8")
    client = TestClient(create_app(ui_dist_dir=tmp_path, platform_registry=PlatformRegistry()))
    assert client.get("/").text == "<h1>ui</h1>"
    assert client.get("/api/health").status_code == 200


def test_missing_ui_dir_is_not_mounted(tmp_path: Path) -> None:
    client = TestClient(
        create_app(ui_dist_dir=tmp_path / "nope", platform_registry=PlatformRegistry())
    )
    assert client.get("/").status_code == 404


def test_factory_reads_ui_dir_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("from env", encoding="utf-8")
    monkeypatch.setenv("VP_UI_DIST_DIR", str(tmp_path))
    client = TestClient(create_app_from_settings())
    assert client.get("/").text == "from env"


def test_unknown_api_route_returns_404() -> None:
    client = TestClient(create_app(platform_registry=PlatformRegistry()))
    assert client.get("/api/nope").status_code == 404


def test_platforms_lists_all_enum_members() -> None:
    client = TestClient(create_app(platform_registry=PlatformRegistry()))
    resp = client.get("/api/platforms")
    assert resp.status_code == 200
    data = resp.json()
    assert [p["id"] for p in data] == ["youtube", "tiktok", "instagram", "facebook", "telegram"]
    assert all(p["available"] is False for p in data)
    assert data[0]["name"] == "YouTube / Shorts"
