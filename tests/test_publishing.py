from collections.abc import Callable
from concurrent.futures import Executor, Future
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from video_publisher.api import create_app
from video_publisher.auth.secret_store import MemorySecretStore, SecretName
from video_publisher.domain import Platform, PostFormat, Publication, Video
from video_publisher.platforms import PlatformRegistry
from video_publisher.platforms.base import PlatformAdapter, PublishResult, ValidationResult
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.telegram_bot import TelegramBotAdapter
from video_publisher.publishing import (
    BuiltAdapter,
    PublishingService,
    PublishRequest,
    default_adapter_factory,
)
from video_publisher.storage.models import PublicationRow
from video_publisher.user_settings import SettingsPatch, SettingsService, TelegramPatch
from video_publisher.user_settings.models import TelegramMode

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"


class InlineExecutor(Executor):
    """Runs submitted work immediately, so tests see the final status."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class DeferredExecutor(Executor):
    """Keeps work until run_all(), to observe the intermediate 'publishing' state."""

    def __init__(self) -> None:
        self.jobs: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.jobs.append((fn, args))
        return Future()

    def run_all(self) -> None:
        for fn, args in self.jobs:
            fn(*args)


class FakeAdapter(PlatformAdapter):
    platform = Platform.TELEGRAM
    supported_formats = (PostFormat.VIDEO,)

    def __init__(
        self,
        *,
        errors: list[str] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.errors = errors or []
        self.fail_with = fail_with
        self.published: list[Publication] = []

    @property
    def supports_native_scheduling(self) -> bool:
        return False

    def authenticate(self) -> None:
        pass

    def validate(self, video: Video) -> ValidationResult:
        return ValidationResult(ok=not self.errors, errors=self.errors)

    def publish(self, publication: Publication) -> PublishResult:
        self.published.append(publication)
        if self.fail_with:
            raise self.fail_with
        return PublishResult(external_id="42", url="https://t.me/ch/42")

    def schedule(self, publication: Publication, at: datetime) -> PublishResult:
        raise NotImplementedError


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")
    return path


def make_service(
    settings_service: SettingsService,
    adapter: PlatformAdapter,
    executor: Executor | None = None,
) -> PublishingService:
    return PublishingService(
        settings_service.session_factory,
        settings_service,
        adapter_factory=lambda _p, _s: BuiltAdapter(adapter, "bot", "@ch"),
        executor=executor or InlineExecutor(),
    )


def request(video: Path, **kw: Any) -> PublishRequest:
    return PublishRequest(video_path=str(video), platform=Platform.TELEGRAM, **kw)


# --- service -------------------------------------------------------------------------


def test_successful_publication(settings_service: SettingsService, video: Path) -> None:
    adapter = FakeAdapter()
    service = make_service(settings_service, adapter)

    view = service.start(
        request(video, title=" Hi ", description="Text ", hashtags=["#a", "b", "a", " "])
    )

    assert view.status == "publishing"  # returned before the upload finished
    final = service.get(view.id)
    assert final is not None
    assert final.status == "published"
    assert final.url == "https://t.me/ch/42"
    assert final.error_code is None
    sent = adapter.published[0]
    assert sent.text.title == "Hi"
    assert sent.text.description == "Text"
    assert sent.text.hashtags == ["a", "b"]  # cleaned and de-duplicated
    assert sent.video.path == video


def test_status_is_publishing_while_upload_runs(
    settings_service: SettingsService, video: Path
) -> None:
    executor = DeferredExecutor()
    service = make_service(settings_service, FakeAdapter(), executor)
    view = service.start(request(video))
    assert service.get(view.id).status == "publishing"  # type: ignore[union-attr]
    executor.run_all()
    assert service.get(view.id).status == "published"  # type: ignore[union-attr]


def test_publish_error_is_stored_with_params(
    settings_service: SettingsService, video: Path
) -> None:
    adapter = FakeAdapter(fail_with=PublishError("telegram_rate_limited", retry_after=30))
    service = make_service(settings_service, adapter)
    view = service.start(request(video))
    final = service.get(view.id)
    assert final is not None
    assert final.status == "failed"
    assert final.error_code == "telegram_rate_limited"
    assert final.error_params == {"retry_after": 30}
    assert final.url is None


def test_unexpected_exception_becomes_internal_error(
    settings_service: SettingsService, video: Path
) -> None:
    service = make_service(settings_service, FakeAdapter(fail_with=RuntimeError("bug")))
    view = service.start(request(video))
    final = service.get(view.id)
    assert final is not None
    assert (final.status, final.error_code) == ("failed", "internal_error")


def test_validation_error_creates_no_record(settings_service: SettingsService, video: Path) -> None:
    adapter = FakeAdapter(errors=["telegram_unsupported_format", "telegram_bot_file_too_large"])
    service = make_service(settings_service, adapter)
    with pytest.raises(PublishError) as info:
        service.start(request(video))
    assert info.value.code == "telegram_unsupported_format"
    assert info.value.params == {
        "errors": ["telegram_unsupported_format", "telegram_bot_file_too_large"]
    }
    assert service.recent() == []
    assert adapter.published == []


def test_record_deleted_during_upload_is_ignored(
    settings_service: SettingsService, video: Path
) -> None:
    executor = DeferredExecutor()
    service = make_service(settings_service, FakeAdapter(), executor)
    view = service.start(request(video))
    with settings_service.session_factory.begin() as session:
        session.delete(session.get(PublicationRow, view.id))
    executor.run_all()  # must not crash
    assert service.get(view.id) is None


def test_recent_is_newest_first_and_limited(settings_service: SettingsService, video: Path) -> None:
    service = make_service(settings_service, FakeAdapter())
    ids = [service.start(request(video, title=str(i))).id for i in range(3)]
    recent = service.recent(limit=2)
    assert [v.id for v in recent] == [ids[2], ids[1]]


def test_get_unknown_returns_none(settings_service: SettingsService) -> None:
    assert make_service(settings_service, FakeAdapter()).get("nope") is None


def test_recover_interrupted_marks_publishing_as_failed(
    settings_service: SettingsService, video: Path
) -> None:
    executor = DeferredExecutor()  # upload never finishes: the app "was closed"
    service = make_service(settings_service, FakeAdapter(), executor)
    stuck = service.start(request(video))
    done_service = make_service(settings_service, FakeAdapter())
    done = done_service.start(request(video))

    restarted = make_service(settings_service, FakeAdapter())
    assert restarted.recover_interrupted() == 1

    stuck_view = restarted.get(stuck.id)
    assert stuck_view is not None
    assert (stuck_view.status, stuck_view.error_code) == ("failed", "interrupted")
    assert restarted.get(done.id).status == "published"  # type: ignore[union-attr]
    assert restarted.recover_interrupted() == 0


def test_shutdown_stops_executor(settings_service: SettingsService) -> None:
    service = PublishingService(settings_service.session_factory, settings_service)
    service.shutdown()
    with pytest.raises(RuntimeError):
        service._executor.submit(print)


# --- request validation ----------------------------------------------------------------


def test_request_rejects_too_many_hashtags(video: Path) -> None:
    with pytest.raises(ValueError):
        request(video, hashtags=[str(i) for i in range(31)])


def test_request_rejects_empty_path() -> None:
    with pytest.raises(ValueError):
        PublishRequest(video_path="", platform=Platform.TELEGRAM)


# --- default adapter factory ------------------------------------------------------------


def configure_bot(service: SettingsService, secrets: MemorySecretStore) -> None:
    service.update(SettingsPatch(telegram=TelegramPatch(channel="@my_channel")))
    secrets.set(SecretName.TELEGRAM_BOT_TOKEN, TOKEN)


def test_factory_builds_bot_adapter(
    settings_service: SettingsService, secrets: MemorySecretStore
) -> None:
    configure_bot(settings_service, secrets)
    built = default_adapter_factory(Platform.TELEGRAM, settings_service)
    assert isinstance(built.adapter, TelegramBotAdapter)
    assert (built.method, built.target) == ("bot", "@my_channel")


@pytest.mark.parametrize(
    ("channel", "token", "missing"),
    [
        (None, None, ["channel", "bot_token"]),
        ("@my_channel", None, ["bot_token"]),
        (None, TOKEN, ["channel"]),
    ],
)
def test_factory_reports_missing_settings(
    settings_service: SettingsService,
    secrets: MemorySecretStore,
    channel: str | None,
    token: str | None,
    missing: list[str],
) -> None:
    if channel:
        settings_service.update(SettingsPatch(telegram=TelegramPatch(channel=channel)))
    if token:
        secrets.set(SecretName.TELEGRAM_BOT_TOKEN, token)
    with pytest.raises(PublishError) as info:
        default_adapter_factory(Platform.TELEGRAM, settings_service)
    assert info.value.code == "telegram_not_configured"
    assert info.value.params == {"missing": missing}


def test_factory_account_mode_not_available_yet(
    settings_service: SettingsService, secrets: MemorySecretStore
) -> None:
    configure_bot(settings_service, secrets)
    settings_service.update(SettingsPatch(telegram=TelegramPatch(mode=TelegramMode.ACCOUNT)))
    with pytest.raises(PublishError) as info:
        default_adapter_factory(Platform.TELEGRAM, settings_service)
    assert info.value.code == "telegram_account_not_available"


@pytest.mark.parametrize("platform", [p for p in Platform if p is not Platform.TELEGRAM])
def test_factory_other_platforms_not_supported(
    settings_service: SettingsService, platform: Platform
) -> None:
    with pytest.raises(PublishError) as info:
        default_adapter_factory(platform, settings_service)
    assert info.value.code == "platform_not_supported"
    assert info.value.params == {"platform": platform.value}


# --- API -----------------------------------------------------------------------------


@pytest.fixture
def pub_client(settings_service: SettingsService) -> tuple[TestClient, FakeAdapter]:
    adapter = FakeAdapter()
    app = create_app(
        platform_registry=PlatformRegistry(),
        settings_service=settings_service,
        publishing_service=make_service(settings_service, adapter),
    )
    return TestClient(app, raise_server_exceptions=False), adapter


def test_api_publish_flow(pub_client: tuple[TestClient, FakeAdapter], video: Path) -> None:
    client, _ = pub_client
    resp = client.post(
        "/api/publications",
        json={"video_path": str(video), "platform": "telegram", "title": "T", "hashtags": ["x"]},
    )
    assert resp.status_code == 202
    created = resp.json()
    assert created["status"] == "publishing"
    assert created["method"] == "bot"
    assert created["target"] == "@ch"

    final = client.get(f"/api/publications/{created['id']}").json()
    assert final["status"] == "published"
    assert final["url"] == "https://t.me/ch/42"

    listed = client.get("/api/publications").json()
    assert [p["id"] for p in listed] == [created["id"]]


def test_api_validation_error_code(settings_service: SettingsService, video: Path) -> None:
    app = create_app(
        platform_registry=PlatformRegistry(),
        settings_service=settings_service,
        publishing_service=make_service(
            settings_service, FakeAdapter(errors=["telegram_unsupported_format"])
        ),
    )
    resp = TestClient(app).post(
        "/api/publications", json={"video_path": str(video), "platform": "telegram"}
    )
    assert resp.status_code == 422
    assert resp.json() == {
        "code": "telegram_unsupported_format",
        "params": {"errors": ["telegram_unsupported_format"]},
    }


def test_api_not_configured_with_real_factory(client: TestClient, video: Path) -> None:
    resp = client.post("/api/publications", json={"video_path": str(video), "platform": "telegram"})
    assert resp.status_code == 422
    assert resp.json() == {
        "code": "telegram_not_configured",
        "params": {"missing": ["channel", "bot_token"]},
    }


def test_api_missing_file_with_real_adapter(
    client: TestClient,
    settings_service: SettingsService,
    secrets: MemorySecretStore,
    tmp_path: Path,
) -> None:
    configure_bot(settings_service, secrets)
    resp = client.post(
        "/api/publications",
        json={"video_path": str(tmp_path / "gone.mp4"), "platform": "telegram"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "video_not_found"


def test_api_mov_file_with_real_adapter(
    client: TestClient,
    settings_service: SettingsService,
    secrets: MemorySecretStore,
    tmp_path: Path,
) -> None:
    configure_bot(settings_service, secrets)
    path = tmp_path / "clip.mov"
    path.write_bytes(b"x")
    resp = client.post("/api/publications", json={"video_path": str(path), "platform": "telegram"})
    assert resp.json()["code"] == "telegram_unsupported_format"


@pytest.mark.parametrize(
    "body",
    [
        {"platform": "telegram"},
        {"video_path": "", "platform": "telegram"},
        {"video_path": "a.mp4", "platform": "myspace"},
        {"video_path": "a.mp4", "platform": "telegram", "hashtags": "notalist"},
    ],
)
def test_api_bad_request_body(pub_client: tuple[TestClient, FakeAdapter], body: object) -> None:
    client, adapter = pub_client
    resp = client.post("/api/publications", json=body)
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"
    assert adapter.published == []


def test_api_unknown_publication(pub_client: tuple[TestClient, FakeAdapter]) -> None:
    client, _ = pub_client
    resp = client.get("/api/publications/nope")
    assert resp.status_code == 404
    assert resp.json() == {"code": "publication_not_found", "params": {}}


@pytest.mark.parametrize("limit", [0, 101, "x"])
def test_api_list_limit_validation(
    pub_client: tuple[TestClient, FakeAdapter], limit: object
) -> None:
    client, _ = pub_client
    assert client.get(f"/api/publications?limit={limit}").status_code == 422


def test_app_marks_interrupted_uploads_on_start(
    settings_service: SettingsService, video: Path
) -> None:
    executor = DeferredExecutor()
    stuck = make_service(settings_service, FakeAdapter(), executor).start(request(video))
    # A fresh app with default publishing service runs the recovery.
    app = create_app(platform_registry=PlatformRegistry(), settings_service=settings_service)
    with TestClient(app) as client:
        resp = client.get(f"/api/publications/{stuck.id}")
    assert resp.json()["error_code"] == "interrupted"


def test_platforms_endpoint_marks_telegram_available() -> None:
    with TestClient(create_app()) as client:
        data = client.get("/api/platforms").json()
    assert {p["id"]: p["available"] for p in data} == {
        "youtube": False,
        "tiktok": False,
        "instagram": False,
        "facebook": False,
        "telegram": True,
    }
