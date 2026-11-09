from collections.abc import Callable, Iterator
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from video_publisher.api import create_app
from video_publisher.auth.facebook_login import (
    CALLBACK_PATH,
    CALLBACK_TEXTS,
    SCOPES,
    CallbackListener,
    FacebookLoginService,
    FacebookLoginState,
)
from video_publisher.auth.secret_store import MemorySecretStore, SecretName
from video_publisher.platforms import PlatformRegistry
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.facebook_page import GraphClient
from video_publisher.user_settings import FacebookPatch, SettingsPatch, SettingsService

GRAPH = "https://graph.facebook.com/v24.0"
APP_ID = "123456789012"
SECRET = "0123456789abcdef0123456789abcdef"


class FakeListener:
    """Stands in for the HTTP listener; tests call ``handle`` like the browser would."""

    instances: ClassVar[list["FakeListener"]] = []

    def __init__(
        self,
        port: int,
        handle: Callable[[dict[str, str]], bool],
        texts: Callable[[], dict[str, str]],
    ) -> None:
        self.port = port
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
    with respx.mock(base_url=GRAPH, assert_all_called=False) as router:
        yield router


@pytest.fixture
def service(settings_service: SettingsService, opened: list[str]) -> FacebookLoginService:
    FakeListener.instances = []
    return FacebookLoginService(
        settings_service,
        redirect_port=47615,
        graph=GraphClient(sleep=lambda _: None, max_retries=1),
        open_browser=opened.append,
        listener_factory=FakeListener,  # type: ignore[arg-type]
    )


def configure(settings: SettingsService) -> None:
    settings.update(SettingsPatch(facebook=FacebookPatch(app_id=APP_ID)))
    settings.set_secret(SecretName.FACEBOOK_APP_SECRET, SECRET)


def mock_exchange(api: respx.MockRouter, pages: list[dict[str, str]]) -> respx.Route:
    api.get("/oauth/access_token", params={"code": "the-code"}).respond(
        200, json={"access_token": "short-user"}
    )
    api.get("/oauth/access_token", params={"grant_type": "fb_exchange_token"}).respond(
        200, json={"access_token": "long-user"}
    )
    return api.get("/me/accounts").respond(200, json={"data": pages})


def state_of(opened: list[str]) -> str:
    return parse_qs(urlparse(opened[-1]).query)["state"][0]


# --- start ---------------------------------------------------------------------------


def test_status_logged_out(service: FacebookLoginService) -> None:
    status = service.status()
    assert status.state is FacebookLoginState.LOGGED_OUT
    assert status.redirect_uri == f"http://localhost:47615{CALLBACK_PATH}"


def test_start_needs_keys(service: FacebookLoginService) -> None:
    with pytest.raises(PublishError) as exc:
        service.start()
    assert exc.value.code == "facebook_app_not_configured"
    assert exc.value.params["missing"] == ["app_id", "app_secret"]


def test_start_opens_dialog(
    service: FacebookLoginService, settings_service: SettingsService, opened: list[str]
) -> None:
    configure(settings_service)
    status = service.start()
    assert status.state is FacebookLoginState.WAITING
    url = urlparse(opened[0])
    query = parse_qs(url.query)
    assert url.netloc == "www.facebook.com" and url.path == "/v24.0/dialog/oauth"
    assert query["client_id"] == [APP_ID]
    assert query["redirect_uri"] == [f"http://localhost:47615{CALLBACK_PATH}"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == [",".join(SCOPES)]
    assert len(query["state"][0]) > 20
    assert SECRET not in opened[0]


def test_restart_replaces_listener(
    service: FacebookLoginService, settings_service: SettingsService
) -> None:
    configure(settings_service)
    service.start()
    service.start()
    assert FakeListener.instances[0].stopped
    assert not FakeListener.instances[1].stopped


# --- callback ------------------------------------------------------------------------


def test_one_page_logs_in_directly(
    api: respx.MockRouter,
    service: FacebookLoginService,
    settings_service: SettingsService,
    opened: list[str],
    secrets: MemorySecretStore,
) -> None:
    configure(settings_service)
    accounts = mock_exchange(api, [{"id": "55", "name": "My Page", "access_token": "page-tok"}])
    service.start()
    ok = FakeListener.instances[0].handle({"code": "the-code", "state": state_of(opened)})
    assert ok
    assert accounts.calls.last.request.url.params["access_token"] == "long-user"
    status = service.status()
    assert status.state is FacebookLoginState.LOGGED_IN
    assert status.page is not None and status.page.name == "My Page"
    assert secrets.get(SecretName.FACEBOOK_PAGE_TOKEN) == "page-tok"
    fb = settings_service.get().facebook
    assert (fb.page_id, fb.page_name) == ("55", "My Page")
    # Tokens never show up in the status.
    assert "page-tok" not in status.model_dump_json()


def test_several_pages_then_choose(
    api: respx.MockRouter,
    service: FacebookLoginService,
    settings_service: SettingsService,
    opened: list[str],
    secrets: MemorySecretStore,
) -> None:
    configure(settings_service)
    mock_exchange(
        api,
        [
            {"id": "1", "name": "A", "access_token": "tok-a"},
            {"id": "2", "name": "B", "access_token": "tok-b"},
            {"id": "3", "name": "no token"},
        ],
    )
    service.start()
    FakeListener.instances[0].handle({"code": "the-code", "state": state_of(opened)})
    status = service.status()
    assert status.state is FacebookLoginState.CHOOSE_PAGE
    assert [p.id for p in status.pages] == ["1", "2"]
    assert "tok-a" not in status.model_dump_json()
    assert secrets.get(SecretName.FACEBOOK_PAGE_TOKEN) is None

    with pytest.raises(PublishError) as exc:
        service.select_page("999")
    assert exc.value.code == "facebook_page_not_offered"

    status = service.select_page("2")
    assert status.state is FacebookLoginState.LOGGED_IN
    assert secrets.get(SecretName.FACEBOOK_PAGE_TOKEN) == "tok-b"


def test_wrong_state_is_ignored(
    api: respx.MockRouter, service: FacebookLoginService, settings_service: SettingsService
) -> None:
    configure(settings_service)
    route = mock_exchange(api, [])
    service.start()
    assert not FakeListener.instances[0].handle({"code": "the-code", "state": "forged"})
    assert not route.called
    assert service.status().state is FacebookLoginState.WAITING


def test_user_denied(
    service: FacebookLoginService, settings_service: SettingsService, opened: list[str]
) -> None:
    configure(settings_service)
    service.start()
    handled = FakeListener.instances[0].handle(
        {"error": "access_denied", "state": state_of(opened)}
    )
    assert not handled
    status = service.status()
    assert status.state is FacebookLoginState.LOGGED_OUT
    assert status.error == "facebook_login_denied"


def test_no_pages(
    api: respx.MockRouter,
    service: FacebookLoginService,
    settings_service: SettingsService,
    opened: list[str],
) -> None:
    configure(settings_service)
    mock_exchange(api, [])
    service.start()
    FakeListener.instances[0].handle({"code": "the-code", "state": state_of(opened)})
    assert service.status().error == "facebook_no_pages"


def test_wrong_secret(
    api: respx.MockRouter,
    service: FacebookLoginService,
    settings_service: SettingsService,
    opened: list[str],
) -> None:
    configure(settings_service)
    api.get("/oauth/access_token").respond(
        400, json={"error": {"message": "Error validating client secret", "code": 1}}
    )
    service.start()
    FakeListener.instances[0].handle({"code": "the-code", "state": state_of(opened)})
    status = service.status()
    assert status.state is FacebookLoginState.LOGGED_OUT
    assert status.error == "facebook_unavailable"


def test_second_callback_after_finish_is_ignored(
    api: respx.MockRouter,
    service: FacebookLoginService,
    settings_service: SettingsService,
    opened: list[str],
) -> None:
    configure(settings_service)
    mock_exchange(api, [{"id": "1", "name": "A", "access_token": "t"}])
    service.start()
    state = state_of(opened)
    listener = FakeListener.instances[0]
    assert listener.handle({"code": "the-code", "state": state})
    assert not listener.handle({"code": "the-code", "state": state})


# --- cancel / logout -------------------------------------------------------------------


def test_cancel_stops_waiting(
    service: FacebookLoginService, settings_service: SettingsService
) -> None:
    configure(settings_service)
    service.start()
    status = service.cancel()
    assert status.state is FacebookLoginState.LOGGED_OUT
    assert FakeListener.instances[0].stopped


def test_logout_forgets_page_keeps_app(
    service: FacebookLoginService,
    settings_service: SettingsService,
    secrets: MemorySecretStore,
) -> None:
    configure(settings_service)
    settings_service.set_secret(SecretName.FACEBOOK_PAGE_TOKEN, "tok")
    settings_service.set_facebook_page("1", "A")
    assert service.status().state is FacebookLoginState.LOGGED_IN
    with pytest.raises(PublishError) as exc:
        service.start()
    assert exc.value.code == "facebook_already_logged_in"

    status = service.logout()
    assert status.state is FacebookLoginState.LOGGED_OUT
    assert secrets.get(SecretName.FACEBOOK_PAGE_TOKEN) is None
    assert secrets.get(SecretName.FACEBOOK_APP_SECRET) == SECRET
    fb = settings_service.get().facebook
    assert fb.page_id is None and fb.app_id == APP_ID


def test_callback_texts_have_same_keys() -> None:
    keys = {lang: set(texts) for lang, texts in CALLBACK_TEXTS.items()}
    assert set(keys) == {"ru", "en"}
    assert keys["ru"] == keys["en"]


def test_callback_page_language(
    service: FacebookLoginService, settings_service: SettingsService
) -> None:
    configure(settings_service)
    service.start()
    texts = FakeListener.instances[0].texts
    assert texts() == CALLBACK_TEXTS["ru"]
    settings_service.update(SettingsPatch(language="en"))
    assert texts() == CALLBACK_TEXTS["en"]


# --- the real listener ---------------------------------------------------------------


def test_real_listener_serves_callback() -> None:
    seen: list[dict[str, str]] = []

    def handle(query: dict[str, str]) -> bool:
        seen.append(query)
        return query.get("code") == "c"

    listener = CallbackListener(0, handle, lambda: CALLBACK_TEXTS["en"])
    try:
        base = f"http://127.0.0.1:{listener.port}"
        with respx.mock(assert_all_mocked=False) as router:
            router.route(host="127.0.0.1").pass_through()
            ok = httpx.get(f"{base}{CALLBACK_PATH}?code=c&state=s")
            bad = httpx.get(f"{base}{CALLBACK_PATH}?error=x")
            missing = httpx.get(f"{base}/other")
        assert ok.status_code == 200 and "logged in" in ok.text
        assert "Login failed" in bad.text
        assert missing.status_code == 404
        assert seen[0] == {"code": "c", "state": "s"}
    finally:
        listener.stop()


def test_port_busy() -> None:
    first = CallbackListener(0, lambda _: True, lambda: CALLBACK_TEXTS["en"])
    try:
        with pytest.raises(PublishError) as exc:
            CallbackListener(first.port, lambda _: True, lambda: CALLBACK_TEXTS["en"])
        assert exc.value.code == "facebook_port_busy"
    finally:
        first.stop()


# --- API -----------------------------------------------------------------------------


@pytest.fixture
def fb_client(settings_service: SettingsService, service: FacebookLoginService) -> TestClient:
    app = create_app(
        platform_registry=PlatformRegistry(),
        settings_service=settings_service,
        facebook_login_service=service,
    )
    return TestClient(app, raise_server_exceptions=False)


def test_api_flow(
    api: respx.MockRouter,
    fb_client: TestClient,
    settings_service: SettingsService,
    opened: list[str],
) -> None:
    assert fb_client.get("/api/facebook/account").json()["state"] == "logged_out"
    resp = fb_client.post("/api/facebook/account/login")
    assert resp.status_code == 422
    assert resp.json()["code"] == "facebook_app_not_configured"

    configure(settings_service)
    assert fb_client.post("/api/facebook/account/login").json()["state"] == "waiting"
    mock_exchange(
        api,
        [
            {"id": "1", "name": "A", "access_token": "t1"},
            {"id": "2", "name": "B", "access_token": "t2"},
        ],
    )
    FakeListener.instances[-1].handle({"code": "the-code", "state": state_of(opened)})
    body = fb_client.get("/api/facebook/account").json()
    assert body["state"] == "choose_page"
    assert "t1" not in str(body)

    resp = fb_client.post("/api/facebook/account/select-page", json={"page_id": "9"})
    assert resp.status_code == 409
    body = fb_client.post("/api/facebook/account/select-page", json={"page_id": "1"}).json()
    assert body["state"] == "logged_in" and body["page"]["name"] == "A"

    settings = fb_client.get("/api/settings").json()
    assert settings["facebook"]["page_id"] == "1"
    assert settings["secrets"]["facebook_page_token"] is True

    assert fb_client.post("/api/facebook/account/logout").json()["state"] == "logged_out"
    assert fb_client.post("/api/facebook/account/cancel").json()["state"] == "logged_out"


def test_api_app_secret_write_only(fb_client: TestClient) -> None:
    resp = fb_client.put("/api/secrets/facebook_app_secret", json={"value": "nope"})
    assert resp.json()["code"] == "invalid_app_secret"
    assert (
        fb_client.put("/api/secrets/facebook_app_secret", json={"value": SECRET}).status_code == 204
    )
    body = fb_client.get("/api/settings").json()
    assert body["secrets"]["facebook_app_secret"] is True
    assert SECRET not in str(body)
    # The page token comes only from logging in.
    resp = fb_client.put("/api/secrets/facebook_page_token", json={"value": "x"})
    assert resp.status_code == 422


def test_api_app_id_validation(fb_client: TestClient) -> None:
    resp = fb_client.patch("/api/settings", json={"facebook": {"app_id": "abc"}})
    assert resp.json()["params"]["fields"] == {"body.facebook.app_id": "invalid_app_id"}
    resp = fb_client.patch("/api/settings", json={"facebook": {"app_id": f" {APP_ID} "}})
    assert resp.json()["facebook"]["app_id"] == APP_ID
    resp = fb_client.patch("/api/settings", json={"facebook": {"app_id": ""}})
    assert resp.json()["facebook"]["app_id"] is None
