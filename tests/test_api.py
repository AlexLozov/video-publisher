from fastapi.testclient import TestClient

from video_publisher import __version__
from video_publisher.api import create_app
from video_publisher.platforms import PlatformRegistry


def test_health() -> None:
    client = TestClient(create_app(platform_registry=PlatformRegistry()))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_platforms_lists_all_enum_members() -> None:
    client = TestClient(create_app(platform_registry=PlatformRegistry()))
    resp = client.get("/api/platforms")
    assert resp.status_code == 200
    data = resp.json()
    assert [p["id"] for p in data] == ["youtube", "tiktok", "instagram", "facebook", "telegram"]
    assert all(p["available"] is False for p in data)
    assert data[0]["name"] == "YouTube / Shorts"
