"""Publishing to a Facebook Page through the publishing service (adapter mocked by respx)."""

from collections.abc import Iterator
from concurrent.futures import Executor, Future
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from video_publisher.auth.secret_store import MemorySecretStore, SecretName
from video_publisher.domain import Platform
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.facebook_page import FacebookPageAdapter
from video_publisher.publishing import PublishingService
from video_publisher.publishing.service import PublishRequest, default_adapter_factory
from video_publisher.storage.models import PublicationRow
from video_publisher.user_settings import SettingsService

VIDEO = "https://graph-video.facebook.com/v24.0"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


class InlineExecutor(Executor):
    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def logged_in(settings_service: SettingsService, secrets: MemorySecretStore) -> None:
    secrets.set(SecretName.FACEBOOK_PAGE_TOKEN, "page-token")
    settings_service.set_facebook_page("55", "My Page")


@pytest.fixture
def service(settings_service: SettingsService) -> PublishingService:
    return PublishingService(
        settings_service.session_factory,
        settings_service,
        executor=InlineExecutor(),
        now=lambda: NOW,
    )


@pytest.fixture
def video(tmp_path: Path) -> str:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 100)
    return str(path)


def test_factory_needs_login(settings_service: SettingsService) -> None:
    with pytest.raises(PublishError) as exc:
        default_adapter_factory(Platform.FACEBOOK, settings_service)
    assert exc.value.code == "facebook_not_configured"


@pytest.mark.usefixtures("logged_in")
def test_factory_builds_adapter(settings_service: SettingsService) -> None:
    built = default_adapter_factory(Platform.FACEBOOK, settings_service)
    assert isinstance(built.adapter, FacebookPageAdapter)
    assert (built.method, built.target) == ("page", "My Page")


@pytest.mark.usefixtures("logged_in")
def test_publish_now(api: respx.MockRouter, service: PublishingService, video: str) -> None:
    api.post(f"{VIDEO}/55/videos").respond(200, json={"id": "777"})
    view = service.start(PublishRequest(video_path=video, platform=Platform.FACEBOOK, title="Hi"))
    done = service.get(view.id)
    assert done is not None
    assert done.status == "published"
    assert done.url == "https://www.facebook.com/55/videos/777"


@pytest.mark.usefixtures("logged_in")
def test_schedule(api: respx.MockRouter, service: PublishingService, video: str) -> None:
    route = api.post(f"{VIDEO}/55/videos").respond(200, json={"id": "9"})
    at = NOW + timedelta(days=2)
    view = service.start(
        PublishRequest(video_path=video, platform=Platform.FACEBOOK, scheduled_at=at)
    )
    done = service.get(view.id)
    assert done is not None and done.status == "scheduled"
    assert str(int(at.timestamp())).encode() in route.calls.last.request.read()


@pytest.mark.usefixtures("logged_in")
@pytest.mark.parametrize(
    ("delta", "code"),
    [(timedelta(minutes=5), "schedule_in_past"), (timedelta(days=31), "schedule_too_far")],
)
def test_schedule_window(
    service: PublishingService, video: str, delta: timedelta, code: str
) -> None:
    with pytest.raises(PublishError) as exc:
        service.start(
            PublishRequest(video_path=video, platform=Platform.FACEBOOK, scheduled_at=NOW + delta)
        )
    assert exc.value.code == code
    if code == "schedule_in_past":
        assert exc.value.params == {"min_minutes": 10}


@pytest.mark.usefixtures("logged_in")
def test_expired_token_is_forgotten(
    api: respx.MockRouter,
    service: PublishingService,
    video: str,
    secrets: MemorySecretStore,
    settings_service: SettingsService,
) -> None:
    api.post(f"{VIDEO}/55/videos").mock(
        return_value=httpx.Response(400, json={"error": {"code": 190, "message": "expired"}})
    )
    view = service.start(PublishRequest(video_path=video, platform=Platform.FACEBOOK))
    done = service.get(view.id)
    assert done is not None and done.error_code == "facebook_token_invalid"
    assert secrets.get(SecretName.FACEBOOK_PAGE_TOKEN) is None
    with settings_service.session_factory() as session:
        row = session.scalars(select(PublicationRow)).one()
        assert "page-token" not in str(row.error_params)


@pytest.mark.usefixtures("logged_in")
def test_delete_scheduled_cancels_on_facebook(
    api: respx.MockRouter, service: PublishingService, video: str
) -> None:
    api.post(f"{VIDEO}/55/videos").respond(200, json={"id": "9"})
    cancel = api.delete("https://graph.facebook.com/v24.0/9").respond(200, json={"success": True})
    view = service.start(
        PublishRequest(
            video_path=video, platform=Platform.FACEBOOK, scheduled_at=NOW + timedelta(hours=1)
        )
    )
    assert service.delete(view.id)
    assert cancel.called
