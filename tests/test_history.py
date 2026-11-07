"""History management: deleting records (withdrawing scheduled posts) and clearing."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from telethon import errors

from fake_telegram_account import SESSION_OK, FakeTelegramAccount
from tests.test_publishing import DeferredExecutor, FakeAdapter, InlineExecutor
from video_publisher.api import create_app
from video_publisher.domain import Platform, Publication
from video_publisher.platforms import PlatformRegistry
from video_publisher.platforms.base import PublishResult
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.telegram_account import TelegramAccountAdapter
from video_publisher.publishing import BuiltAdapter, PublishingService, PublishRequest
from video_publisher.storage.models import PublicationRow
from video_publisher.user_settings import SettingsService


class SchedulingAdapter(FakeAdapter):
    """Schedules natively and records cancellations."""

    max_schedule_ahead = timedelta(days=365 * 100)

    def __init__(self, cancel_error: PublishError | None = None) -> None:
        super().__init__()
        self.cancelled: list[str] = []
        self.cancel_error = cancel_error

    @property
    def supports_native_scheduling(self) -> bool:
        return True

    def schedule(self, publication: Publication, at: datetime) -> PublishResult:
        return PublishResult(external_id="77")

    def cancel_scheduled(self, external_id: str) -> None:
        if self.cancel_error:
            raise self.cancel_error
        self.cancelled.append(external_id)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")
    return path


def make(
    settings_service: SettingsService,
    adapter: FakeAdapter,
    method: str = "account",
    executor: Any = None,
) -> PublishingService:
    return PublishingService(
        settings_service.session_factory,
        settings_service,
        adapter_factory=lambda _p, _s: BuiltAdapter(adapter, method, "@ch"),
        executor=executor or InlineExecutor(),
    )


def req(video: Path, **kw: Any) -> PublishRequest:
    return PublishRequest(video_path=str(video), platform=Platform.TELEGRAM, **kw)


def statuses(settings_service: SettingsService) -> list[str]:
    with settings_service.session_factory() as session:
        return sorted(r.status for r in session.scalars(select(PublicationRow)).all())


LATER = datetime.fromisoformat("2099-01-01T10:00:00+00:00")


# --- delete --------------------------------------------------------------------------


def test_delete_published_record(settings_service: SettingsService, video: Path) -> None:
    adapter = SchedulingAdapter()
    service = make(settings_service, adapter)
    view = service.start(req(video))
    assert service.delete(view.id) is True
    assert service.get(view.id) is None
    assert adapter.cancelled == []  # a published post is never touched


def test_delete_unknown_record(settings_service: SettingsService) -> None:
    assert make(settings_service, SchedulingAdapter()).delete("nope") is False


def test_delete_scheduled_withdraws_it_first(
    settings_service: SettingsService, video: Path
) -> None:
    adapter = SchedulingAdapter()
    service = make(settings_service, adapter)
    view = service.start(req(video, scheduled_at=LATER))
    assert service.get(view.id).status == "scheduled"  # type: ignore[union-attr]
    assert service.delete(view.id) is True
    assert adapter.cancelled == ["77"]
    assert service.get(view.id) is None


def test_delete_scheduled_keeps_record_when_withdraw_fails(
    settings_service: SettingsService, video: Path
) -> None:
    adapter = SchedulingAdapter()
    service = make(settings_service, adapter)
    view = service.start(req(video, scheduled_at=LATER))
    adapter.cancel_error = PublishError("network_error")
    with pytest.raises(PublishError) as info:
        service.delete(view.id)
    assert info.value.code == "network_error"
    assert service.get(view.id) is not None


def test_delete_scheduled_after_switching_method(
    settings_service: SettingsService, video: Path
) -> None:
    adapter = SchedulingAdapter()
    view = make(settings_service, adapter, method="account").start(req(video, scheduled_at=LATER))
    with pytest.raises(PublishError) as info:
        make(settings_service, adapter, method="bot").delete(view.id)
    assert info.value.code == "cancel_needs_method"
    assert info.value.params == {"method": "account"}
    assert adapter.cancelled == []


def test_delete_refuses_running_upload(settings_service: SettingsService, video: Path) -> None:
    service = make(settings_service, SchedulingAdapter(), executor=DeferredExecutor())
    view = service.start(req(video))
    with pytest.raises(PublishError) as info:
        service.delete(view.id)
    assert info.value.code == "publication_in_progress"


def test_base_adapter_cannot_cancel() -> None:
    with pytest.raises(PublishError) as info:
        FakeAdapter().cancel_scheduled("1")
    assert info.value.code == "cancel_not_supported"
    assert info.value.params == {"platform": "telegram"}


# --- clear ---------------------------------------------------------------------------


def test_clear_keeps_scheduled_and_running(settings_service: SettingsService, video: Path) -> None:
    adapter = SchedulingAdapter()
    service = make(settings_service, adapter)
    service.start(req(video))  # published
    service.start(req(video, scheduled_at=LATER))  # scheduled
    failing = make(settings_service, FakeAdapter(fail_with=PublishError("network_error")))
    failing.start(req(video))  # failed
    make(settings_service, adapter, executor=DeferredExecutor()).start(req(video))  # running

    assert service.clear() == 2
    assert statuses(settings_service) == ["publishing", "scheduled"]
    assert adapter.cancelled == []


def test_clear_empty_history(settings_service: SettingsService) -> None:
    assert make(settings_service, SchedulingAdapter()).clear() == 0


# --- API -----------------------------------------------------------------------------


def client_for(service: PublishingService, settings_service: SettingsService) -> TestClient:
    app = create_app(
        platform_registry=PlatformRegistry(),
        settings_service=settings_service,
        publishing_service=service,
    )
    return TestClient(app, raise_server_exceptions=False)


def test_api_delete_and_clear(settings_service: SettingsService, video: Path) -> None:
    adapter = SchedulingAdapter()
    client = client_for(make(settings_service, adapter), settings_service)
    a = client.post("/api/publications", json={"video_path": str(video), "platform": "telegram"})
    b = client.post("/api/publications", json={"video_path": str(video), "platform": "telegram"})

    assert client.delete(f"/api/publications/{a.json()['id']}").status_code == 204
    assert client.delete(f"/api/publications/{a.json()['id']}").json() == {
        "code": "publication_not_found",
        "params": {},
    }
    resp = client.delete("/api/publications")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 1}
    assert client.get(f"/api/publications/{b.json()['id']}").status_code == 404


def test_api_delete_errors(settings_service: SettingsService, video: Path) -> None:
    running = make(settings_service, SchedulingAdapter(), executor=DeferredExecutor())
    client = client_for(running, settings_service)
    pub = client.post("/api/publications", json={"video_path": str(video), "platform": "telegram"})
    resp = client.delete(f"/api/publications/{pub.json()['id']}")
    assert resp.status_code == 409
    assert resp.json()["code"] == "publication_in_progress"

    adapter = SchedulingAdapter(cancel_error=PublishError("telegram_session_expired"))
    client = client_for(make(settings_service, adapter), settings_service)
    pub = client.post(
        "/api/publications",
        json={"video_path": str(video), "platform": "telegram", "scheduled_at": LATER.isoformat()},
    )
    resp = client.delete(f"/api/publications/{pub.json()['id']}")
    assert resp.status_code == 422
    assert resp.json()["code"] == "telegram_session_expired"


def test_api_list_allows_large_limit(settings_service: SettingsService) -> None:
    client = client_for(make(settings_service, SchedulingAdapter()), settings_service)
    assert client.get("/api/publications?limit=1000").status_code == 200
    assert client.get("/api/publications?limit=1001").status_code == 422


# --- Telegram account: withdrawing a scheduled post ----------------------------------


def account(world: FakeTelegramAccount, on_invalid: Any = None) -> TelegramAccountAdapter:
    return TelegramAccountAdapter(
        api_id=4242,
        api_hash="0123456789abcdef0123456789abcdef",
        session=SESSION_OK,
        channel="@my_channel",
        client_factory=world.factory,
        on_session_invalid=on_invalid,
    )


def test_telegram_cancel_scheduled() -> None:
    world = FakeTelegramAccount()
    account(world).cancel_scheduled("123")
    assert world.deleted_scheduled == [("peer:@my_channel", [123])]
    assert all(c.disconnected for c in world.clients)


def test_telegram_cancel_bad_id() -> None:
    world = FakeTelegramAccount()
    with pytest.raises(PublishError) as info:
        account(world).cancel_scheduled("abc")
    assert info.value.code == "telegram_rejected"
    assert world.calls == []


def test_telegram_cancel_network_error() -> None:
    world = FakeTelegramAccount()
    world.fail("invoke", ConnectionError("down"))
    with pytest.raises(PublishError) as info:
        account(world).cancel_scheduled("5")
    assert info.value.code == "network_error"


def test_telegram_cancel_with_revoked_session_forgets_it() -> None:
    world = FakeTelegramAccount()
    world.authorized_sessions.clear()
    forgotten: list[bool] = []
    with pytest.raises(PublishError) as info:
        account(world, on_invalid=lambda: forgotten.append(True)).cancel_scheduled("5")
    assert info.value.code == "telegram_session_expired"
    assert forgotten == [True]


def test_telegram_cancel_without_rights() -> None:
    world = FakeTelegramAccount()
    world.fail("invoke", errors.ChatAdminRequiredError(request=None))
    with pytest.raises(PublishError) as info:
        account(world).cancel_scheduled("5")
    assert info.value.code == "telegram_account_not_admin"
