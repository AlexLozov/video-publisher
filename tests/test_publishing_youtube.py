"""Publishing to YouTube through the publishing service (HTTP mocked by respx)."""

import json
import shutil
from collections.abc import Iterator
from concurrent.futures import Executor, Future
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from video_publisher.api import create_app
from video_publisher.auth.secret_store import MemorySecretStore, SecretName
from video_publisher.domain import Platform
from video_publisher.platforms import PlatformRegistry
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.youtube import API_BASE, TOKEN_URL, UPLOAD_URL, YouTubeAdapter
from video_publisher.publishing import PublishingService
from video_publisher.publishing.service import (
    BatchPublishRequest,
    PublishRequest,
    default_adapter_factory,
)
from video_publisher.storage.models import PublicationRow
from video_publisher.user_settings import (
    SettingsPatch,
    SettingsService,
    TelegramPatch,
    YouTubePatch,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
CLIENT_ID = "123456789012-abcdefghij0123456789.apps.googleusercontent.com"
SESSION_URL = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=s1"
MEDIA = Path(__file__).parent / "media"


class InlineExecutor(Executor):
    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).respond(200, json={"access_token": "access-1"})
        yield router


def mock_upload(api: respx.MockRouter, video_id: str = "vid1") -> respx.Route:
    init = api.post(url__startswith=UPLOAD_URL).respond(200, headers={"Location": SESSION_URL})
    api.put(SESSION_URL).respond(200, json={"id": video_id})
    return init


@pytest.fixture
def logged_in(settings_service: SettingsService, secrets: MemorySecretStore) -> None:
    settings_service.update(
        SettingsPatch(
            youtube=YouTubePatch(
                client_id=CLIENT_ID, privacy="unlisted", category_id="27", tags=["cats"]
            )
        )
    )
    secrets.set(SecretName.YOUTUBE_CLIENT_SECRET, "GOCSPX-abcdefghijklmnopqrstuv")
    secrets.set(SecretName.YOUTUBE_REFRESH_TOKEN, "refresh-1")
    settings_service.set_youtube_channel("UC1", "My Channel")


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
    shutil.copy(MEDIA / "landscape.mp4", path)
    return str(path)


@pytest.fixture
def portrait(tmp_path: Path) -> str:
    path = tmp_path / "short.mp4"
    shutil.copy(MEDIA / "rotated.mp4", path)  # a phone video: rotated landscape frame
    return str(path)


def test_factory_reports_what_is_missing(
    settings_service: SettingsService, secrets: MemorySecretStore
) -> None:
    with pytest.raises(PublishError) as exc:
        default_adapter_factory(Platform.YOUTUBE, settings_service)
    assert exc.value.code == "youtube_not_configured"
    assert exc.value.params == {"missing": ["client_id", "client_secret", "login"]}

    settings_service.update(SettingsPatch(youtube=YouTubePatch(client_id=CLIENT_ID)))
    secrets.set(SecretName.YOUTUBE_CLIENT_SECRET, "GOCSPX-abcdefghijklmnopqrstuv")
    secrets.set(SecretName.YOUTUBE_REFRESH_TOKEN, "refresh-1")  # but no channel yet
    with pytest.raises(PublishError) as exc:
        default_adapter_factory(Platform.YOUTUBE, settings_service)
    assert exc.value.params == {"missing": ["login"]}


@pytest.mark.usefixtures("logged_in")
def test_factory_builds_adapter(settings_service: SettingsService) -> None:
    built = default_adapter_factory(Platform.YOUTUBE, settings_service)
    assert isinstance(built.adapter, YouTubeAdapter)
    assert (built.method, built.target) == ("channel", "My Channel")


@pytest.mark.usefixtures("logged_in")
def test_publish_now_uses_settings_defaults(
    api: respx.MockRouter, service: PublishingService, video: str
) -> None:
    init = mock_upload(api)
    view = service.start(
        PublishRequest(video_path=video, platform=Platform.YOUTUBE, title="Hi", hashtags=["#a"])
    )
    done = service.get(view.id)
    assert done is not None and done.status == "published"
    assert done.url == "https://www.youtube.com/watch?v=vid1"
    body = json.loads(init.calls.last.request.content)
    assert body["snippet"]["categoryId"] == "27"
    assert body["snippet"]["tags"] == ["cats"]
    assert body["snippet"]["description"] == "#a"
    assert body["status"]["privacyStatus"] == "unlisted"


@pytest.mark.usefixtures("logged_in")
def test_phone_video_goes_out_as_short(
    api: respx.MockRouter, service: PublishingService, portrait: str
) -> None:
    mock_upload(api, "s9")
    view = service.start(PublishRequest(video_path=portrait, platform=Platform.YOUTUBE))
    done = service.get(view.id)
    assert done is not None and done.url == "https://www.youtube.com/shorts/s9"


@pytest.mark.usefixtures("logged_in")
def test_schedule(api: respx.MockRouter, service: PublishingService, video: str) -> None:
    init = mock_upload(api)
    at = NOW + timedelta(days=3)
    view = service.start(
        PublishRequest(video_path=video, platform=Platform.YOUTUBE, scheduled_at=at)
    )
    done = service.get(view.id)
    assert done is not None and done.status == "scheduled"
    status = json.loads(init.calls.last.request.content)["status"]
    assert status["publishAt"] == "2026-11-12T12:00:00Z"
    assert status["privacyStatus"] == "private"


@pytest.mark.usefixtures("logged_in")
@pytest.mark.parametrize(
    ("delta", "code"),
    [(timedelta(minutes=10), "schedule_in_past"), (timedelta(days=366), "schedule_too_far")],
)
def test_schedule_window(
    api: respx.MockRouter, service: PublishingService, video: str, delta: timedelta, code: str
) -> None:
    init = mock_upload(api)
    with pytest.raises(PublishError) as exc:
        service.start(
            PublishRequest(video_path=video, platform=Platform.YOUTUBE, scheduled_at=NOW + delta)
        )
    assert exc.value.code == code
    assert not init.called


@pytest.mark.usefixtures("logged_in")
def test_long_title_is_refused_before_upload(
    api: respx.MockRouter, service: PublishingService, video: str, settings_service: SettingsService
) -> None:
    init = mock_upload(api)
    with pytest.raises(PublishError) as exc:
        service.start(PublishRequest(video_path=video, platform=Platform.YOUTUBE, title="x" * 101))
    assert exc.value.code == "youtube_title_too_long"
    assert not init.called
    with settings_service.session_factory() as session:
        assert session.scalars(select(PublicationRow)).all() == []


@pytest.mark.usefixtures("logged_in")
def test_revoked_login_is_forgotten(
    service: PublishingService, video: str, secrets: MemorySecretStore
) -> None:
    # The login is checked before anything starts: no record, the user logs in again.
    with respx.mock() as api:
        api.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})
        with pytest.raises(PublishError) as exc:
            service.start(PublishRequest(video_path=video, platform=Platform.YOUTUBE))
    assert exc.value.code == "youtube_token_invalid"
    assert service.recent() == []
    assert secrets.get(SecretName.YOUTUBE_REFRESH_TOKEN) is None
    assert secrets.get(SecretName.YOUTUBE_CLIENT_SECRET) is not None


@pytest.mark.usefixtures("logged_in")
def test_dead_login_stops_the_whole_batch(
    service: PublishingService, video: str, settings_service: SettingsService
) -> None:
    """Telegram + YouTube with a dead YouTube login: nothing goes out anywhere."""
    settings_service.update(SettingsPatch(telegram=TelegramPatch(channel="@my_channel")))
    settings_service.set_secret(
        SecretName.TELEGRAM_BOT_TOKEN, "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
    )
    with respx.mock(assert_all_called=False) as api:
        api.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})
        tg = api.post(url__startswith="https://api.telegram.org").respond(200, json={"ok": True})
        with pytest.raises(PublishError) as exc:
            service.start_batch(
                BatchPublishRequest(
                    video_path=video, platforms=[Platform.TELEGRAM, Platform.YOUTUBE]
                )
            )
    assert exc.value.code == "youtube_token_invalid"
    assert exc.value.params["platform"] == "youtube"
    assert not tg.called
    assert service.recent() == []


@pytest.mark.usefixtures("logged_in")
def test_offline_check_blocks_youtube_start(service: PublishingService, video: str) -> None:
    with respx.mock() as api:
        api.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("offline"))
        with pytest.raises(PublishError) as exc:
            service.start(PublishRequest(video_path=video, platform=Platform.YOUTUBE))
    assert exc.value.code == "network_error"


@pytest.mark.usefixtures("logged_in")
def test_quota_error_is_recorded_without_secrets(
    api: respx.MockRouter, service: PublishingService, video: str, settings_service: SettingsService
) -> None:
    api.post(url__startswith=UPLOAD_URL).mock(
        return_value=httpx.Response(
            403, json={"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}
        )
    )
    view = service.start(PublishRequest(video_path=video, platform=Platform.YOUTUBE))
    done = service.get(view.id)
    assert done is not None and done.status == "failed"
    assert done.error_code == "youtube_quota_exceeded"
    with settings_service.session_factory() as session:
        row = session.scalars(select(PublicationRow)).one()
        dump = str(row.error_params) + row.title + row.description
    assert "refresh-1" not in dump and "access-1" not in dump and "GOCSPX" not in dump


@pytest.mark.usefixtures("logged_in")
def test_delete_scheduled_deletes_on_youtube(
    api: respx.MockRouter, service: PublishingService, video: str
) -> None:
    mock_upload(api, "vid7")
    view = service.start(
        PublishRequest(
            video_path=video, platform=Platform.YOUTUBE, scheduled_at=NOW + timedelta(days=1)
        )
    )
    delete = api.delete(f"{API_BASE}/videos", params={"id": "vid7"}).respond(204)
    assert service.delete(view.id)
    assert delete.called
    assert service.get(view.id) is None


@pytest.mark.usefixtures("logged_in")
def test_delete_scheduled_keeps_record_if_youtube_refuses(
    api: respx.MockRouter, service: PublishingService, video: str
) -> None:
    mock_upload(api, "vid7")
    view = service.start(
        PublishRequest(
            video_path=video, platform=Platform.YOUTUBE, scheduled_at=NOW + timedelta(days=1)
        )
    )
    api.delete(f"{API_BASE}/videos").respond(503)
    with pytest.raises(PublishError) as exc:
        service.delete(view.id)
    assert exc.value.code == "youtube_unavailable"
    assert service.get(view.id) is not None


# --- video info endpoint ------------------------------------------------------------------


@pytest.fixture
def client(settings_service: SettingsService) -> TestClient:
    app = create_app(platform_registry=PlatformRegistry(), settings_service=settings_service)
    return TestClient(app, raise_server_exceptions=False)


def test_video_info(client: TestClient, portrait: str, video: str, tmp_path: Path) -> None:
    resp = client.get("/api/publications/video-info", params={"path": portrait})
    assert resp.json() == {
        "duration_seconds": 4.0,
        "width": 180,
        "height": 320,
        "youtube_short": True,
    }
    assert (
        client.get("/api/publications/video-info", params={"path": video}).json()["youtube_short"]
        is False
    )
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"\x00" * 10)
    assert client.get("/api/publications/video-info", params={"path": str(junk)}).json() == {
        "duration_seconds": None,
        "width": None,
        "height": None,
        "youtube_short": None,
    }
    missing = client.get("/api/publications/video-info", params={"path": str(tmp_path / "x")})
    assert missing.status_code == 404
    assert missing.json()["code"] == "video_not_found"
