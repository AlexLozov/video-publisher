from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_publisher.api import create_app
from video_publisher.auth.secret_store import MemorySecretStore
from video_publisher.platforms import PlatformRegistry
from video_publisher.storage import create_db_engine, create_session_factory, upgrade_database
from video_publisher.user_settings import SettingsService


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never touch the real data dir or the real OS credential store from tests."""
    monkeypatch.setenv("VP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VP_SECRET_BACKEND", "memory")


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    upgrade_database(url)
    return url


@pytest.fixture
def secrets() -> MemorySecretStore:
    return MemorySecretStore()


@pytest.fixture
def settings_service(db_url: str, secrets: MemorySecretStore) -> Iterator[SettingsService]:
    engine = create_db_engine(db_url)
    yield SettingsService(create_session_factory(engine), secrets)
    engine.dispose()


@pytest.fixture
def client(settings_service: SettingsService) -> TestClient:
    app = create_app(platform_registry=PlatformRegistry(), settings_service=settings_service)
    return TestClient(app, raise_server_exceptions=False)
