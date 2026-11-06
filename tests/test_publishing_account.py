"""Publishing service with Telegram "my account" mode and native scheduling."""

from collections.abc import Callable
from concurrent.futures import Executor, Future
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fake_telegram_account import SESSION_OK, FakeTelegramAccount
from video_publisher.api import create_app
from video_publisher.auth.secret_store import MemorySecretStore, SecretName
from video_publisher.domain import Platform, PostFormat, Publication, Video
from video_publisher.platforms import PlatformRegistry
from video_publisher.platforms.base import PlatformAdapter, PublishResult, ValidationResult
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.telegram_account import TelegramAccountAdapter
from video_publisher.publishing import (
    BuiltAdapter,
    PublishingService,
    PublishRequest,
    make_adapter_factory,
)
from video_publisher.storage.models import PublicationRow
from video_publisher.user_settings import SettingsPatch, SettingsService, TelegramPatch
from video_publisher.user_settings.models import TelegramMode

API_HASH = "0123456789abcdef0123456789abcdef"
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
MSK = timezone(timedelta(hours=3))


class InlineExecutor(Executor):
    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class SchedulingAdapter(PlatformAdapter):
    platform = Platform.TELEGRAM
    supported_formats = (PostFormat.VIDEO,)
    max_schedule_ahead = timedelta(days=365)

    def __init__(self, native: bool = True, fail: PublishError | None = None) -> None:
        self.native = native
        self.fail = fail
        self.scheduled: list[tuple[Publication, datetime]] = []
        self.published: list[Publication] = []

    @property
    def supports_native_scheduling(self) -> bool:
        return self.native

    def authenticate(self) -> None:
        pass

    def validate(self, video: Video) -> ValidationResult:
        return ValidationResult(ok=True)

    def publish(self, publication: Publication) -> PublishResult:
        self.published.append(publication)
        return PublishResult(external_id="1", url="https://t.me/ch/1")

    def schedule(self, publication: Publication, at: datetime) -> PublishResult:
        if self.fail:
            raise self.fail
        self.scheduled.append((publication, at))
        return PublishResult(external_id="7", url=None)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")
    return path


def service_with(settings: SettingsService, adapter: PlatformAdapter, method: str = "account"):
    return PublishingService(
        settings.session_factory,
        settings,
        adapter_factory=lambda _p, _s: BuiltAdapter(adapter, method, "@ch"),
        executor=InlineExecutor(),
        now=lambda: NOW,
    )


def req(video: Path, **kw: Any) -> PublishRequest:
    return PublishRequest(video_path=str(video), platform=Platform.TELEGRAM, **kw)


# --- scheduling in the service ----------------------------------------------------------


def test_scheduled_publication(settings_service: SettingsService, video: Path) -> None:
    adapter = SchedulingAdapter()
    service = service_with(settings_service, adapter)
    at = datetime(2026, 11, 10, 18, 30, tzinfo=MSK)

    view = service.start(req(video, scheduled_at=at))

    assert view.scheduled_at == at  # same instant
    final = service.get(view.id)
    assert final is not None
    assert final.status == "scheduled"
    assert final.url is None
    assert final.scheduled_at == datetime(2026, 11, 10, 15, 30, tzinfo=UTC)
    assert adapter.published == []
    assert adapter.scheduled[0][1] == at
    with settings_service.session_factory() as session:
        row = session.get(PublicationRow, view.id)
        assert row is not None and row.external_id == "7"


def test_publish_now_has_no_schedule(settings_service: SettingsService, video: Path) -> None:
    adapter = SchedulingAdapter()
    view = service_with(settings_service, adapter).start(req(video))
    assert view.scheduled_at is None
    assert len(adapter.published) == 1 and adapter.scheduled == []


@pytest.mark.parametrize(
    ("at", "code"),
    [
        (NOW - timedelta(hours=1), "schedule_in_past"),
        (NOW + timedelta(seconds=10), "schedule_in_past"),
        (NOW + timedelta(days=400), "schedule_too_far"),
    ],
)
def test_bad_schedule_time_creates_no_record(
    settings_service: SettingsService, video: Path, at: datetime, code: str
) -> None:
    service = service_with(settings_service, SchedulingAdapter())
    with pytest.raises(PublishError) as info:
        service.start(req(video, scheduled_at=at))
    assert info.value.code == code
    assert service.recent() == []


def test_scheduling_needs_native_support(settings_service: SettingsService, video: Path) -> None:
    service = service_with(settings_service, SchedulingAdapter(native=False), method="bot")
    with pytest.raises(PublishError) as info:
        service.start(req(video, scheduled_at=NOW + timedelta(days=1)))
    assert (info.value.code, info.value.params) == ("scheduling_not_supported", {"method": "bot"})
    assert service.recent() == []


def test_schedule_failure_is_stored(settings_service: SettingsService, video: Path) -> None:
    adapter = SchedulingAdapter(fail=PublishError("telegram_schedule_limit"))
    service = service_with(settings_service, adapter)
    view = service.start(req(video, scheduled_at=NOW + timedelta(days=1)))
    final = service.get(view.id)
    assert final is not None
    assert (final.status, final.error_code) == ("failed", "telegram_schedule_limit")


def test_request_requires_time_zone(video: Path) -> None:
    with pytest.raises(ValueError, match="timezone"):
        req(video, scheduled_at=datetime(2026, 12, 1, 10, 0))


def test_scheduled_rows_survive_restart_recovery(
    settings_service: SettingsService, video: Path
) -> None:
    service = service_with(settings_service, SchedulingAdapter())
    view = service.start(req(video, scheduled_at=NOW + timedelta(days=1)))
    assert service.recover_interrupted() == 0
    final = service.get(view.id)
    assert final is not None and final.status == "scheduled"


# --- adapter factory: account mode ------------------------------------------------------


def configure_account(
    settings: SettingsService, secrets: MemorySecretStore, *, login: bool = True
) -> None:
    settings.update(
        SettingsPatch(
            telegram=TelegramPatch(mode=TelegramMode.ACCOUNT, channel="@my_channel", api_id=4242)
        )
    )
    secrets.set(SecretName.TELEGRAM_API_HASH, API_HASH)
    if login:
        secrets.set(SecretName.TELEGRAM_SESSION, SESSION_OK)


def test_factory_builds_account_adapter(
    settings_service: SettingsService, secrets: MemorySecretStore
) -> None:
    configure_account(settings_service, secrets)
    built = make_adapter_factory(FakeTelegramAccount().factory)(Platform.TELEGRAM, settings_service)
    assert isinstance(built.adapter, TelegramAccountAdapter)
    assert (built.method, built.target) == ("account", "@my_channel")


@pytest.mark.parametrize(
    ("channel", "api_id", "api_hash", "session", "missing"),
    [
        (None, None, None, None, ["channel", "api_id", "api_hash", "login"]),
        ("@my_channel", 4242, API_HASH, None, ["login"]),
        ("@my_channel", None, API_HASH, SESSION_OK, ["api_id"]),
        (None, 4242, None, SESSION_OK, ["channel", "api_hash"]),
    ],
)
def test_factory_account_missing_settings(
    settings_service: SettingsService,
    secrets: MemorySecretStore,
    channel: str | None,
    api_id: int | None,
    api_hash: str | None,
    session: str | None,
    missing: list[str],
) -> None:
    settings_service.update(
        SettingsPatch(
            telegram=TelegramPatch(mode=TelegramMode.ACCOUNT, channel=channel, api_id=api_id)
        )
    )
    if api_hash:
        secrets.set(SecretName.TELEGRAM_API_HASH, api_hash)
    if session:
        secrets.set(SecretName.TELEGRAM_SESSION, session)
    with pytest.raises(PublishError) as info:
        make_adapter_factory()(Platform.TELEGRAM, settings_service)
    assert (info.value.code, info.value.params) == (
        "telegram_account_not_configured",
        {"missing": missing},
    )


# --- end to end through the API with a fake Telegram ------------------------------------


@pytest.fixture
def world() -> FakeTelegramAccount:
    return FakeTelegramAccount()


@pytest.fixture
def api(settings_service: SettingsService, world: FakeTelegramAccount) -> TestClient:
    app = create_app(
        platform_registry=PlatformRegistry(),
        settings_service=settings_service,
        publishing_service=PublishingService(
            settings_service.session_factory,
            settings_service,
            adapter_factory=make_adapter_factory(world.factory),
            executor=InlineExecutor(),
        ),
        telegram_client_factory=world.factory,
    )
    return TestClient(app, raise_server_exceptions=False)


def test_api_account_publish_now(
    api: TestClient,
    world: FakeTelegramAccount,
    settings_service: SettingsService,
    secrets: MemorySecretStore,
    video: Path,
) -> None:
    configure_account(settings_service, secrets)
    resp = api.post(
        "/api/publications", json={"video_path": str(video), "platform": "telegram", "title": "T"}
    )
    assert resp.status_code == 202
    assert resp.json()["method"] == "account"
    final = api.get(f"/api/publications/{resp.json()['id']}").json()
    assert final["status"] == "published"
    assert final["url"] == "https://t.me/my_channel/100"
    assert world.sent[0]["caption"] == "T"


def test_api_account_schedule(
    api: TestClient,
    world: FakeTelegramAccount,
    settings_service: SettingsService,
    secrets: MemorySecretStore,
    video: Path,
) -> None:
    configure_account(settings_service, secrets)
    at = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    resp = api.post(
        "/api/publications",
        json={
            "video_path": str(video),
            "platform": "telegram",
            "scheduled_at": at.astimezone(MSK).isoformat(),
        },
    )
    assert resp.status_code == 202
    final = api.get(f"/api/publications/{resp.json()['id']}").json()
    assert final["status"] == "scheduled"
    assert final["url"] is None
    assert datetime.fromisoformat(final["scheduled_at"]) == at
    assert world.sent[0]["schedule"] == at


def test_api_naive_schedule_time_is_a_field_error(
    api: TestClient, settings_service: SettingsService, secrets: MemorySecretStore, video: Path
) -> None:
    configure_account(settings_service, secrets)
    resp = api.post(
        "/api/publications",
        json={
            "video_path": str(video),
            "platform": "telegram",
            "scheduled_at": "2030-01-01T10:00:00",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["params"]["fields"] == {"body.scheduled_at": "timezone_aware"}


def test_api_schedule_in_bot_mode(
    api: TestClient, settings_service: SettingsService, secrets: MemorySecretStore, video: Path
) -> None:
    settings_service.update(SettingsPatch(telegram=TelegramPatch(channel="@my_channel")))
    secrets.set(SecretName.TELEGRAM_BOT_TOKEN, "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ")
    at = datetime.now(UTC) + timedelta(days=1)
    resp = api.post(
        "/api/publications",
        json={"video_path": str(video), "platform": "telegram", "scheduled_at": at.isoformat()},
    )
    assert resp.status_code == 422
    assert resp.json() == {"code": "scheduling_not_supported", "params": {"method": "bot"}}


def test_api_revoked_session_logs_out(
    api: TestClient,
    world: FakeTelegramAccount,
    settings_service: SettingsService,
    secrets: MemorySecretStore,
    video: Path,
) -> None:
    configure_account(settings_service, secrets)
    world.authorized_sessions.clear()  # ended on another device
    resp = api.post("/api/publications", json={"video_path": str(video), "platform": "telegram"})
    final = api.get(f"/api/publications/{resp.json()['id']}").json()
    assert (final["status"], final["error_code"]) == ("failed", "telegram_session_expired")
    assert secrets.get(SecretName.TELEGRAM_SESSION) is None
    assert api.get("/api/telegram/account").json()["state"] == "logged_out"


def test_api_account_not_configured(
    api: TestClient, settings_service: SettingsService, video: Path
) -> None:
    settings_service.update(SettingsPatch(telegram=TelegramPatch(mode=TelegramMode.ACCOUNT)))
    resp = api.post("/api/publications", json={"video_path": str(video), "platform": "telegram"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "telegram_account_not_configured"


def test_secrets_never_in_publication_records(
    api: TestClient,
    settings_service: SettingsService,
    secrets: MemorySecretStore,
    video: Path,
) -> None:
    configure_account(settings_service, secrets)
    api.post("/api/publications", json={"video_path": str(video), "platform": "telegram"})
    listed = api.get("/api/publications").text
    with settings_service.session_factory() as session:
        rows = repr([vars(r) for r in session.query(PublicationRow).all()])
    for secret in (API_HASH, SESSION_OK):
        assert secret not in listed
        assert secret not in rows
