"""Google login for YouTube: consent URL with PKCE, code exchange, channel, logout, API."""

import base64
import hashlib
from collections.abc import Callable, Iterator
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from video_publisher.api import create_app
from video_publisher.auth.oauth_callback import CALLBACK_TEXTS, CallbackListener
from video_publisher.auth.secret_store import MemorySecretStore, SecretName
from video_publisher.auth.youtube_login import (
    SCOPE,
    YouTubeLoginService,
    YouTubeLoginState,
    pkce_pair,
)
from video_publisher.platforms import PlatformRegistry
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.youtube import API_BASE, TOKEN_URL, GoogleClient
from video_publisher.user_settings import SettingsPatch, SettingsService, YouTubePatch

CLIENT_ID = "123456789012-abcdefghij0123456789.apps.googleusercontent.com"
SECRET = "GOCSPX-abcdefghijklmnopqrstuv"


class FakeListener:
    instances: ClassVar[list["FakeListener"]] = []

    def __init__(
        self,
        port: int,
        handle: Callable[[dict[str, str]], bool],
        texts: Callable[[], dict[str, str]],
    ) -> None:
        self.port = 51234
        self.requested_port = port
        self.handle = handle
        self.texts = texts
        self.stopped = False
        FakeListener.instances.append(self)

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def opened() -> list[str]:
    return []


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def service(settings_service: SettingsService, opened: list[str]) -> YouTubeLoginService:
    FakeListener.instances = []
    return YouTubeLoginService(
        settings_service,
        google=GoogleClient(sleep=lambda _: None, max_retries=1),
        open_browser=opened.append,
        listener_factory=FakeListener,  # type: ignore[arg-type]
    )


def configure(settings: SettingsService) -> None:
    settings.update(SettingsPatch(youtube=YouTubePatch(client_id=CLIENT_ID)))
    settings.set_secret(SecretName.YOUTUBE_CLIENT_SECRET, SECRET)


def query_of(opened: list[str]) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(opened[-1]).query).items()}


def mock_google(
    api: respx.MockRouter,
    *,
    scope: str = SCOPE,
    refresh: str | None = "refresh-tok",
    channels: list[dict[str, object]] | None = None,
) -> tuple[respx.Route, respx.Route]:
    tokens: dict[str, str] = {"access_token": "access-tok", "scope": scope}
    if refresh:
        tokens["refresh_token"] = refresh
    token = api.post(TOKEN_URL).respond(200, json=tokens)
    if channels is None:
        channels = [{"id": "UC123", "snippet": {"title": "My Channel"}}]
    chan = api.get(f"{API_BASE}/channels").respond(200, json={"items": channels})
    return token, chan


def login(service: YouTubeLoginService, opened: list[str]) -> bool:
    service.start()
    return FakeListener.instances[-1].handle(
        {"code": "the-code", "state": query_of(opened)["state"]}
    )


# --- start --------------------------------------------------------------------------------


def test_status_logged_out(service: YouTubeLoginService) -> None:
    assert service.status().state is YouTubeLoginState.LOGGED_OUT


def test_start_needs_keys(service: YouTubeLoginService) -> None:
    with pytest.raises(PublishError) as exc:
        service.start()
    assert exc.value.code == "youtube_app_not_configured"
    assert exc.value.params["missing"] == ["client_id", "client_secret"]


def test_start_opens_consent_page(
    service: YouTubeLoginService, settings_service: SettingsService, opened: list[str]
) -> None:
    configure(settings_service)
    status = service.start()
    assert status.state is YouTubeLoginState.WAITING
    assert FakeListener.instances[0].requested_port == 0  # any free port
    url = urlparse(opened[0])
    q = query_of(opened)
    assert url.netloc == "accounts.google.com" and url.path == "/o/oauth2/v2/auth"
    assert q["client_id"] == CLIENT_ID
    assert q["redirect_uri"] == "http://127.0.0.1:51234/"
    assert q["scope"] == SCOPE
    assert q["access_type"] == "offline"
    assert q["code_challenge_method"] == "S256"
    assert len(q["code_challenge"]) == 43
    assert SECRET not in opened[0]


def test_pkce_pair_matches() -> None:
    verifier, challenge = pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == challenge
    assert 43 <= len(verifier) <= 128


def test_restart_replaces_listener(
    service: YouTubeLoginService, settings_service: SettingsService
) -> None:
    configure(settings_service)
    service.start()
    service.start()
    assert FakeListener.instances[0].stopped
    assert not FakeListener.instances[1].stopped


# --- callback -----------------------------------------------------------------------------


def test_login_saves_refresh_token_and_channel(
    api: respx.MockRouter,
    service: YouTubeLoginService,
    settings_service: SettingsService,
    opened: list[str],
    secrets: MemorySecretStore,
) -> None:
    configure(settings_service)
    token, chan = mock_google(api)
    assert login(service, opened)

    form = parse_qs(token.calls.last.request.content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["redirect_uri"] == ["http://127.0.0.1:51234/"]
    assert form["code_verifier"][0]  # PKCE
    assert chan.calls.last.request.headers["Authorization"] == "Bearer access-tok"
    status = service.status()
    assert status.state is YouTubeLoginState.LOGGED_IN
    assert status.channel is not None and status.channel.title == "My Channel"
    assert secrets.get(SecretName.YOUTUBE_REFRESH_TOKEN) == "refresh-tok"
    yt = settings_service.get().youtube
    assert (yt.channel_id, yt.channel_title) == ("UC123", "My Channel")
    dump = status.model_dump_json()
    assert "refresh-tok" not in dump and "access-tok" not in dump


def test_wrong_state_is_ignored(
    api: respx.MockRouter,
    service: YouTubeLoginService,
    settings_service: SettingsService,
) -> None:
    configure(settings_service)
    token, _ = mock_google(api)
    service.start()
    assert not FakeListener.instances[0].handle({"code": "c", "state": "forged"})
    assert not token.called
    assert service.status().state is YouTubeLoginState.WAITING


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ({"scope": "openid"}, "youtube_scope_missing"),
        ({"refresh": None}, "youtube_no_refresh_token"),
        ({"channels": []}, "youtube_no_channel"),
    ],
)
def test_login_problems(
    api: respx.MockRouter,
    service: YouTubeLoginService,
    settings_service: SettingsService,
    opened: list[str],
    secrets: MemorySecretStore,
    setup: dict[str, object],
    code: str,
) -> None:
    configure(settings_service)
    mock_google(api, **setup)  # type: ignore[arg-type]
    assert not login(service, opened)
    status = service.status()
    assert status.state is YouTubeLoginState.LOGGED_OUT
    assert status.error == code
    assert secrets.get(SecretName.YOUTUBE_REFRESH_TOKEN) is None


def test_user_cancels_in_browser(
    service: YouTubeLoginService, settings_service: SettingsService, opened: list[str]
) -> None:
    configure(settings_service)
    service.start()
    state = query_of(opened)["state"]
    assert not FakeListener.instances[0].handle({"error": "access_denied", "state": state})
    assert service.status().error == "youtube_login_denied"


def test_wrong_client_secret(
    api: respx.MockRouter,
    service: YouTubeLoginService,
    settings_service: SettingsService,
    opened: list[str],
) -> None:
    configure(settings_service)
    api.post(TOKEN_URL).respond(401, json={"error": "invalid_client"})
    assert not login(service, opened)
    assert service.status().error == "youtube_client_invalid"


def test_already_logged_in(
    api: respx.MockRouter,
    service: YouTubeLoginService,
    settings_service: SettingsService,
    opened: list[str],
) -> None:
    configure(settings_service)
    mock_google(api)
    login(service, opened)
    with pytest.raises(PublishError) as exc:
        service.start()
    assert exc.value.code == "youtube_already_logged_in"


def test_logout_keeps_client_keys(
    api: respx.MockRouter,
    service: YouTubeLoginService,
    settings_service: SettingsService,
    opened: list[str],
    secrets: MemorySecretStore,
) -> None:
    configure(settings_service)
    mock_google(api)
    login(service, opened)
    status = service.logout()
    assert status.state is YouTubeLoginState.LOGGED_OUT
    assert secrets.get(SecretName.YOUTUBE_REFRESH_TOKEN) is None
    assert secrets.get(SecretName.YOUTUBE_CLIENT_SECRET) == SECRET
    yt = settings_service.get().youtube
    assert yt.channel_id is None and yt.client_id == CLIENT_ID


def test_cancel_stops_waiting(
    service: YouTubeLoginService, settings_service: SettingsService
) -> None:
    configure(settings_service)
    service.start()
    assert service.cancel().state is YouTubeLoginState.LOGGED_OUT
    assert FakeListener.instances[0].stopped
    service.shutdown()


def test_real_listener_on_free_port() -> None:
    got: list[dict[str, str]] = []

    def handle(q: dict[str, str]) -> bool:
        got.append(q)
        return True

    listener = CallbackListener(
        0, handle, lambda: CALLBACK_TEXTS["ru"], path="/", busy_code="youtube_port_busy"
    )
    try:
        resp = httpx.get(f"http://127.0.0.1:{listener.port}/?code=c&state=s")
        assert resp.status_code == 200
        assert "Вход выполнен" in resp.text
        assert httpx.get(f"http://127.0.0.1:{listener.port}/favicon.ico").status_code == 404
    finally:
        listener.stop()
    assert got == [{"code": "c", "state": "s"}]


# --- API ----------------------------------------------------------------------------------


@pytest.fixture
def client(settings_service: SettingsService, service: YouTubeLoginService) -> TestClient:
    app = create_app(
        platform_registry=PlatformRegistry(),
        settings_service=settings_service,
        youtube_login_service=service,
    )
    return TestClient(app, raise_server_exceptions=False)


def test_api_flow(
    api: respx.MockRouter,
    client: TestClient,
    settings_service: SettingsService,
    opened: list[str],
) -> None:
    assert client.get("/api/youtube/account").json()["state"] == "logged_out"
    resp = client.post("/api/youtube/account/login")
    assert resp.status_code == 422
    assert resp.json()["code"] == "youtube_app_not_configured"

    configure(settings_service)
    assert client.post("/api/youtube/account/login").json()["state"] == "waiting"
    mock_google(api)
    FakeListener.instances[-1].handle({"code": "c", "state": query_of(opened)["state"]})
    body = client.get("/api/youtube/account").json()
    assert body == {
        "state": "logged_in",
        "channel": {"id": "UC123", "title": "My Channel"},
        "error": None,
    }
    assert client.post("/api/youtube/account/login").status_code == 409
    assert client.post("/api/youtube/account/logout").json()["state"] == "logged_out"
    assert client.post("/api/youtube/account/cancel").json()["state"] == "logged_out"
