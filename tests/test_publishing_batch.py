"""One upload to several networks: all checked first, shared batch_id, one card in history."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text
from tests.test_publishing import DeferredExecutor, FakeAdapter, InlineExecutor

from video_publisher.api import create_app
from video_publisher.domain import Platform
from video_publisher.platforms import PlatformRegistry
from video_publisher.platforms.errors import PublishError
from video_publisher.publishing import (
    BatchPublishRequest,
    BuiltAdapter,
    PublishingService,
)
from video_publisher.storage import create_db_engine, create_session_factory, upgrade_database
from video_publisher.storage.models import PublicationRow
from video_publisher.user_settings import SettingsService


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")
    return path


def service_with(
    settings_service: SettingsService,
    adapters: dict[Platform, FakeAdapter | PublishError],
    executor: Any = None,
) -> PublishingService:
    def factory(platform: Platform, _settings: SettingsService) -> BuiltAdapter:
        item = adapters[platform]
        if isinstance(item, PublishError):
            raise item
        return BuiltAdapter(item, "fake", f"@{platform.value}")

    return PublishingService(
        settings_service.session_factory,
        settings_service,
        adapter_factory=factory,
        executor=executor or InlineExecutor(),
    )


def batch(video: Path, *platforms: Platform, **kw: Any) -> BatchPublishRequest:
    return BatchPublishRequest(video_path=str(video), platforms=list(platforms), **kw)


def rows(settings_service: SettingsService) -> list[PublicationRow]:
    with settings_service.session_factory() as session:
        return list(session.scalars(select(PublicationRow)).all())


def test_batch_publishes_to_every_network_with_one_batch_id(
    settings_service: SettingsService, video: Path
) -> None:
    tg, yt = FakeAdapter(), FakeAdapter()
    service = service_with(settings_service, {Platform.TELEGRAM: tg, Platform.YOUTUBE: yt})

    views = service.start_batch(batch(video, Platform.TELEGRAM, Platform.YOUTUBE, title="T"))

    assert [v.platform for v in views] == [Platform.TELEGRAM, Platform.YOUTUBE]
    assert len({v.batch_id for v in views}) == 1
    assert len(tg.published) == 1 and len(yt.published) == 1
    stored = rows(settings_service)
    assert {r.status for r in stored} == {"published"}
    assert {r.target for r in stored} == {"@telegram", "@youtube"}
    assert {r.title for r in stored} == {"T"}


def test_batch_drops_repeated_networks(settings_service: SettingsService, video: Path) -> None:
    tg = FakeAdapter()
    service = service_with(settings_service, {Platform.TELEGRAM: tg})
    views = service.start_batch(batch(video, Platform.TELEGRAM, Platform.TELEGRAM))
    assert len(views) == 1
    assert len(tg.published) == 1


def test_batch_problem_with_one_network_starts_nothing(
    settings_service: SettingsService, video: Path
) -> None:
    tg = FakeAdapter()
    service = service_with(
        settings_service,
        {Platform.TELEGRAM: tg, Platform.YOUTUBE: FakeAdapter(errors=["video_not_found"])},
    )
    with pytest.raises(PublishError) as info:
        service.start_batch(batch(video, Platform.TELEGRAM, Platform.YOUTUBE))
    assert info.value.code == "video_not_found"
    assert info.value.params["platform"] == "youtube"
    assert tg.published == []
    assert rows(settings_service) == []


def test_batch_not_configured_network_names_it(
    settings_service: SettingsService, video: Path
) -> None:
    service = service_with(
        settings_service,
        {Platform.TELEGRAM: PublishError("telegram_not_configured", missing=["channel"])},
    )
    with pytest.raises(PublishError) as info:
        service.start_batch(batch(video, Platform.TELEGRAM))
    assert info.value.params == {"missing": ["channel"], "platform": "telegram"}


def test_batch_rows_are_publishing_until_workers_finish(
    settings_service: SettingsService, video: Path
) -> None:
    executor = DeferredExecutor()
    service = service_with(
        settings_service,
        {Platform.TELEGRAM: FakeAdapter(), Platform.YOUTUBE: FakeAdapter()},
        executor,
    )
    views = service.start_batch(batch(video, Platform.TELEGRAM, Platform.YOUTUBE))
    assert {v.status for v in views} == {"publishing"}
    executor.run_all()
    assert {r.status for r in rows(settings_service)} == {"published"}


def test_single_start_gets_its_own_batch(settings_service: SettingsService, video: Path) -> None:
    service = service_with(settings_service, {Platform.TELEGRAM: FakeAdapter()})
    a = service.start_batch(batch(video, Platform.TELEGRAM))[0]
    b = service.start_batch(batch(video, Platform.TELEGRAM))[0]
    assert a.batch_id != b.batch_id


def test_batch_request_needs_a_network(video: Path) -> None:
    with pytest.raises(ValidationError):
        BatchPublishRequest(video_path=str(video), platforms=[])


def test_api_batch(settings_service: SettingsService, video: Path) -> None:
    app = create_app(
        platform_registry=PlatformRegistry(),
        settings_service=settings_service,
        publishing_service=service_with(
            settings_service, {Platform.TELEGRAM: FakeAdapter(), Platform.YOUTUBE: FakeAdapter()}
        ),
    )
    client = TestClient(app)
    resp = client.post(
        "/api/publications/batch",
        json={"video_path": str(video), "platforms": ["telegram", "youtube"], "title": "T"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert [p["platform"] for p in body] == ["telegram", "youtube"]
    assert body[0]["batch_id"] == body[1]["batch_id"]


def test_api_batch_error_names_network(settings_service: SettingsService, video: Path) -> None:
    app = create_app(
        platform_registry=PlatformRegistry(),
        settings_service=settings_service,
        publishing_service=service_with(
            settings_service, {Platform.TELEGRAM: FakeAdapter(errors=["video_not_found"])}
        ),
    )
    resp = TestClient(app).post(
        "/api/publications/batch", json={"video_path": str(video), "platforms": ["telegram"]}
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "video_not_found"
    assert resp.json()["params"]["platform"] == "telegram"


@pytest.mark.parametrize(
    "body",
    [
        {"video_path": "x"},
        {"video_path": "x", "platforms": []},
        {"video_path": "x", "platforms": ["myspace"]},
    ],
)
def test_api_batch_bad_body(settings_service: SettingsService, body: object) -> None:
    app = create_app(platform_registry=PlatformRegistry(), settings_service=settings_service)
    resp = TestClient(app).post("/api/publications/batch", json=body)
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


def test_migration_puts_old_publications_in_their_own_batch(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    upgrade_database(url, "0002")
    engine = create_db_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO publications (id, platform, method, video_path, title, description,"
                " hashtags, status, error_params, created_at, updated_at) VALUES ('old-1',"
                " 'telegram', 'bot', 'C:/v.mp4', '', '', '[]', 'published', '{}',"
                " '2026-11-06 00:00:00', '2026-11-06 00:00:00')"
            )
        )
    upgrade_database(url)
    with create_session_factory(engine)() as session:
        row = session.get(PublicationRow, "old-1")
        assert row is not None and row.batch_id == "old-1"
    engine.dispose()
