"""Logging in to the user's own Telegram account: every step and every error path."""

import logging

import pytest
from fastapi.testclient import TestClient
from telethon import errors

from fake_telegram_account import SESSION_OK, FakeTelegramAccount
from video_publisher.api import create_app
from video_publisher.auth.secret_store import MemorySecretStore, SecretName, SecretStoreError
from video_publisher.auth.telegram_login import (
    LoginState,
    TelegramLoginService,
    mask_phone,
    normalize_phone,
)
from video_publisher.platforms import PlatformRegistry
from video_publisher.platforms.errors import PublishError
from video_publisher.storage.models import SettingRow
from video_publisher.user_settings import SettingsPatch, SettingsService, TelegramPatch

API_HASH = "0123456789abcdef0123456789abcdef"
PHONE = "+79991234567"


@pytest.fixture
def world() -> FakeTelegramAccount:
    return FakeTelegramAccount()


def configure_keys(settings: SettingsService) -> None:
    settings.update(SettingsPatch(telegram=TelegramPatch(api_id=4242)))
    settings.set_secret(SecretName.TELEGRAM_API_HASH, API_HASH)


@pytest.fixture
def login(settings_service: SettingsService, world: FakeTelegramAccount) -> TelegramLoginService:
    configure_keys(settings_service)
    return TelegramLoginService(settings_service, world.factory)


def code_of(call: object) -> str:
    with pytest.raises(PublishError) as info:
        call()  # type: ignore[operator]
    return info.value.code


# --- helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+7 999 123-45-67", "+79991234567"),
        ("79991234567", "+79991234567"),
        ("+1 (555) 123.4567", "+15551234567"),
    ],
)
def test_normalize_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "+12", "+1234567890123456", "+7999abc4567"])
def test_normalize_phone_rejects(raw: str) -> None:
    assert code_of(lambda: normalize_phone(raw)) == "telegram_phone_invalid"


def test_mask_phone_hides_the_middle() -> None:
    assert mask_phone(PHONE) == "+7••••••4567"


# --- happy paths ------------------------------------------------------------------------


def test_initial_state_is_logged_out(login: TelegramLoginService) -> None:
    assert login.status().state is LoginState.LOGGED_OUT
    assert login.status().phone is None


def test_login_without_2fa(
    login: TelegramLoginService, world: FakeTelegramAccount, secrets: MemorySecretStore
) -> None:
    status = login.send_code("+7 999 123 45 67")
    assert status.state is LoginState.CODE_SENT
    assert status.phone == "+7••••••4567"
    assert world.called("send_code_request") == [((PHONE,), {})]

    status = login.sign_in(" 123 45 ")
    assert status.state is LoginState.LOGGED_IN
    assert secrets.get(SecretName.TELEGRAM_SESSION) == SESSION_OK
    # The sign-in continues the session that was created when the code was sent.
    sign_in_client = world.clients[1]
    assert sign_in_client.api_id == 4242 and sign_in_client.api_hash == API_HASH
    (args, kwargs) = world.called("sign_in")[0]
    assert args == (PHONE, "12345")
    assert kwargs["phone_code_hash"] == "hash-1"


def test_login_continues_the_session_from_send_code(
    login: TelegramLoginService, world: FakeTelegramAccount
) -> None:
    login.send_code(PHONE)
    login.sign_in("12345")
    assert world.clients[0].session.value == f"half-{PHONE}"
    # Second client was created from the session saved after send_code (DC may differ).
    assert world.clients[1].session.value == SESSION_OK


def test_login_with_2fa(
    login: TelegramLoginService, world: FakeTelegramAccount, secrets: MemorySecretStore
) -> None:
    world.password = "cloud-pass"
    login.send_code(PHONE)
    status = login.sign_in("12345")
    assert status.state is LoginState.PASSWORD_NEEDED
    assert status.phone == "+7••••••4567"
    assert secrets.get(SecretName.TELEGRAM_SESSION) is None

    status = login.check_password("cloud-pass")
    assert status.state is LoginState.LOGGED_IN
    assert secrets.get(SecretName.TELEGRAM_SESSION) == SESSION_OK
    assert world.called("sign_in")[-1][1]["password"] == "cloud-pass"


def test_every_client_is_disconnected(
    login: TelegramLoginService, world: FakeTelegramAccount
) -> None:
    world.password = "pw"
    login.send_code(PHONE)
    login.sign_in("12345")
    login.check_password("pw")
    login.logout()
    assert len(world.clients) == 4
    assert all(c.disconnected for c in world.clients)


def test_cancel_forgets_pending_login(login: TelegramLoginService) -> None:
    login.send_code(PHONE)
    assert login.cancel().state is LoginState.LOGGED_OUT
    assert code_of(lambda: login.sign_in("12345")) == "telegram_login_not_started"


def test_logout_ends_session_remotely_and_locally(
    login: TelegramLoginService, world: FakeTelegramAccount, secrets: MemorySecretStore
) -> None:
    login.send_code(PHONE)
    login.sign_in("12345")
    status = login.logout()
    assert status.state is LoginState.LOGGED_OUT
    assert secrets.get(SecretName.TELEGRAM_SESSION) is None
    assert len(world.called("log_out")) == 1
    assert SESSION_OK not in world.authorized_sessions


@pytest.mark.parametrize(
    "exc", [ConnectionError("offline"), errors.AuthKeyUnregisteredError(request=None)]
)
def test_logout_forgets_session_even_if_telegram_fails(
    login: TelegramLoginService,
    world: FakeTelegramAccount,
    secrets: MemorySecretStore,
    exc: Exception,
) -> None:
    secrets.set(SecretName.TELEGRAM_SESSION, SESSION_OK)
    world.fail("log_out", exc)
    assert login.logout().state is LoginState.LOGGED_OUT
    assert secrets.get(SecretName.TELEGRAM_SESSION) is None


def test_logout_without_api_keys_still_forgets_session(
    settings_service: SettingsService, world: FakeTelegramAccount, secrets: MemorySecretStore
) -> None:
    secrets.set(SecretName.TELEGRAM_SESSION, SESSION_OK)
    service = TelegramLoginService(settings_service, world.factory)
    assert service.logout().state is LoginState.LOGGED_OUT
    assert secrets.get(SecretName.TELEGRAM_SESSION) is None
    assert world.clients == []


def test_logout_when_not_logged_in_is_harmless(
    login: TelegramLoginService, world: FakeTelegramAccount
) -> None:
    assert login.logout().state is LoginState.LOGGED_OUT
    assert world.clients == []


def test_stored_session_means_logged_in(
    login: TelegramLoginService, secrets: MemorySecretStore
) -> None:
    secrets.set(SecretName.TELEGRAM_SESSION, SESSION_OK)
    assert login.status().state is LoginState.LOGGED_IN


# --- errors -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("api_id", "api_hash", "missing"),
    [
        (None, None, ["api_id", "api_hash"]),
        (12345, None, ["api_hash"]),
        (None, API_HASH, ["api_id"]),
    ],
)
def test_missing_api_keys(
    settings_service: SettingsService,
    world: FakeTelegramAccount,
    api_id: int | None,
    api_hash: str | None,
    missing: list[str],
) -> None:
    if api_id:
        settings_service.update(SettingsPatch(telegram=TelegramPatch(api_id=api_id)))
    if api_hash:
        settings_service.set_secret(SecretName.TELEGRAM_API_HASH, api_hash)
    service = TelegramLoginService(settings_service, world.factory)
    with pytest.raises(PublishError) as info:
        service.send_code(PHONE)
    assert info.value.code == "telegram_api_keys_missing"
    assert info.value.params == {"missing": missing}
    assert world.clients == []


def test_invalid_phone_is_rejected_before_calling_telegram(
    login: TelegramLoginService, world: FakeTelegramAccount
) -> None:
    assert code_of(lambda: login.send_code("12")) == "telegram_phone_invalid"
    assert world.calls == []


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (errors.PhoneNumberInvalidError(request=None), "telegram_phone_invalid"),
        (errors.PhoneNumberBannedError(request=None), "telegram_phone_banned"),
        (errors.PhoneNumberFloodError(request=None), "telegram_too_many_attempts"),
        (errors.ApiIdInvalidError(request=None), "telegram_api_keys_invalid"),
        (errors.ApiIdPublishedFloodError(request=None), "telegram_api_keys_invalid"),
        (ConnectionError("offline"), "network_error"),
        (OSError("no route"), "network_error"),
        (TimeoutError(), "network_error"),
        (errors.RPCError(request=None, message="WEIRD"), "telegram_login_failed"),
    ],
)
def test_send_code_errors(
    login: TelegramLoginService, world: FakeTelegramAccount, exc: Exception, code: str
) -> None:
    world.fail("send_code_request", exc)
    assert code_of(lambda: login.send_code(PHONE)) == code
    assert login.status().state is LoginState.LOGGED_OUT
    assert world.clients[0].disconnected


def test_connect_failure_is_network_error(
    login: TelegramLoginService, world: FakeTelegramAccount
) -> None:
    world.fail("connect", ConnectionError("offline"))
    with pytest.raises(PublishError) as info:
        login.send_code(PHONE)
    assert info.value.code == "network_error"
    assert info.value.retryable


def test_flood_wait_reports_seconds(
    login: TelegramLoginService, world: FakeTelegramAccount
) -> None:
    world.fail("send_code_request", errors.FloodWaitError(request=None, capture=420))
    with pytest.raises(PublishError) as info:
        login.send_code(PHONE)
    assert info.value.code == "telegram_flood_wait"
    assert info.value.params == {"seconds": 420}


def test_send_code_when_already_logged_in(
    login: TelegramLoginService, world: FakeTelegramAccount, secrets: MemorySecretStore
) -> None:
    secrets.set(SecretName.TELEGRAM_SESSION, SESSION_OK)
    assert code_of(lambda: login.send_code(PHONE)) == "telegram_already_logged_in"
    assert world.clients == []


def test_wrong_code_keeps_login_going(login: TelegramLoginService) -> None:
    login.send_code(PHONE)
    assert code_of(lambda: login.sign_in("99999")) == "telegram_code_invalid"
    assert login.status().state is LoginState.CODE_SENT
    assert login.sign_in("12345").state is LoginState.LOGGED_IN


@pytest.mark.parametrize("raw", ["", "12", "abcde", "123456789"])
def test_malformed_code_is_rejected_locally(
    login: TelegramLoginService, world: FakeTelegramAccount, raw: str
) -> None:
    login.send_code(PHONE)
    assert code_of(lambda: login.sign_in(raw)) == "telegram_code_invalid"
    assert world.called("sign_in") == []


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (errors.PhoneCodeExpiredError(request=None), "telegram_code_expired"),
        (errors.PhoneHashExpiredError(request=None), "telegram_code_expired"),
        (errors.PhoneNumberUnoccupiedError(request=None), "telegram_phone_not_registered"),
        (errors.PhoneNumberBannedError(request=None), "telegram_phone_banned"),
    ],
)
def test_sign_in_errors_that_restart_login(
    login: TelegramLoginService, world: FakeTelegramAccount, exc: Exception, code: str
) -> None:
    login.send_code(PHONE)
    world.fail("sign_in", exc)
    assert code_of(lambda: login.sign_in("12345")) == code
    assert login.status().state is LoginState.LOGGED_OUT


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (errors.PhoneCodeEmptyError(request=None), "telegram_code_invalid"),
        (errors.FloodWaitError(request=None, capture=30), "telegram_flood_wait"),
        (ConnectionError("offline"), "network_error"),
    ],
)
def test_sign_in_errors_that_keep_login(
    login: TelegramLoginService, world: FakeTelegramAccount, exc: Exception, code: str
) -> None:
    login.send_code(PHONE)
    world.fail("sign_in", exc)
    assert code_of(lambda: login.sign_in("12345")) == code
    assert login.status().state is LoginState.CODE_SENT


def test_sign_in_without_send_code(login: TelegramLoginService) -> None:
    assert code_of(lambda: login.sign_in("12345")) == "telegram_login_not_started"


def test_code_step_is_closed_while_password_is_asked(
    login: TelegramLoginService, world: FakeTelegramAccount
) -> None:
    world.password = "pw"
    login.send_code(PHONE)
    login.sign_in("12345")
    assert code_of(lambda: login.sign_in("12345")) == "telegram_login_not_started"


def test_password_without_2fa_step(login: TelegramLoginService) -> None:
    assert code_of(lambda: login.check_password("pw")) == "telegram_login_not_started"
    login.send_code(PHONE)
    assert code_of(lambda: login.check_password("pw")) == "telegram_login_not_started"


def test_wrong_password_keeps_password_step(
    login: TelegramLoginService, world: FakeTelegramAccount, secrets: MemorySecretStore
) -> None:
    world.password = "right"
    login.send_code(PHONE)
    login.sign_in("12345")
    assert code_of(lambda: login.check_password("wrong")) == "telegram_password_invalid"
    assert login.status().state is LoginState.PASSWORD_NEEDED
    assert secrets.get(SecretName.TELEGRAM_SESSION) is None
    assert login.check_password("right").state is LoginState.LOGGED_IN


@pytest.mark.parametrize("password", ["", "x" * 257])
def test_password_length_checked_locally(
    login: TelegramLoginService, world: FakeTelegramAccount, password: str
) -> None:
    world.password = "pw"
    login.send_code(PHONE)
    login.sign_in("12345")
    calls = len(world.called("sign_in"))
    assert code_of(lambda: login.check_password(password)) == "telegram_password_invalid"
    assert len(world.called("sign_in")) == calls


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (errors.PhonePasswordFloodError(request=None), "telegram_too_many_attempts"),
        (errors.FloodWaitError(request=None, capture=60), "telegram_flood_wait"),
        (ConnectionError("offline"), "network_error"),
    ],
)
def test_password_errors(
    login: TelegramLoginService, world: FakeTelegramAccount, exc: Exception, code: str
) -> None:
    world.password = "pw"
    login.send_code(PHONE)
    login.sign_in("12345")
    world.fail("sign_in", exc)
    assert code_of(lambda: login.check_password("pw")) == code
    assert login.status().state is LoginState.PASSWORD_NEEDED


# --- secrets ----------------------------------------------------------------------------


def test_nothing_secret_lands_in_db_or_logs(
    login: TelegramLoginService,
    world: FakeTelegramAccount,
    settings_service: SettingsService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    world.password = "cloud-pass"
    login.send_code(PHONE)
    login.sign_in("12345")
    world.fail("sign_in", errors.PasswordHashInvalidError(request=None))
    with pytest.raises(PublishError):
        login.check_password("cloud-pass")
    login.check_password("cloud-pass")

    with settings_service.session_factory() as session:
        stored = repr([(r.key, r.value) for r in session.query(SettingRow).all()])
    for secret in (PHONE, "12345", "cloud-pass", SESSION_OK, API_HASH):
        assert secret not in stored
        assert secret not in caplog.text


# --- API --------------------------------------------------------------------------------


@pytest.fixture
def api(settings_service: SettingsService, world: FakeTelegramAccount) -> TestClient:
    configure_keys(settings_service)
    app = create_app(
        platform_registry=PlatformRegistry(),
        settings_service=settings_service,
        telegram_client_factory=world.factory,
    )
    return TestClient(app, raise_server_exceptions=False)


BASE = "/api/telegram/account"


def test_api_full_login_with_2fa_and_logout(api: TestClient, world: FakeTelegramAccount) -> None:
    world.password = "pw"
    assert api.get(BASE).json() == {"state": "logged_out", "phone": None}

    resp = api.post(f"{BASE}/send-code", json={"phone": PHONE})
    assert resp.status_code == 200
    assert resp.json() == {"state": "code_sent", "phone": "+7••••••4567"}

    resp = api.post(f"{BASE}/sign-in", json={"code": "12345"})
    assert resp.json()["state"] == "password_needed"

    resp = api.post(f"{BASE}/password", json={"password": "pw"})
    assert resp.json() == {"state": "logged_in", "phone": None}
    assert api.get("/api/settings").json()["secrets"]["telegram_session"] is True

    resp = api.post(f"{BASE}/logout")
    assert resp.json()["state"] == "logged_out"
    assert api.get("/api/settings").json()["secrets"]["telegram_session"] is False


def test_api_cancel(api: TestClient) -> None:
    api.post(f"{BASE}/send-code", json={"phone": PHONE})
    assert api.post(f"{BASE}/cancel").json()["state"] == "logged_out"


@pytest.mark.parametrize(
    ("exc", "status", "body"),
    [
        (
            errors.FloodWaitError(request=None, capture=90),
            429,
            {"code": "telegram_flood_wait", "params": {"seconds": 90}},
        ),
        (ConnectionError(), 502, {"code": "network_error", "params": {}}),
        (
            errors.PhoneNumberInvalidError(request=None),
            422,
            {"code": "telegram_phone_invalid", "params": {}},
        ),
    ],
)
def test_api_error_codes(
    api: TestClient, world: FakeTelegramAccount, exc: Exception, status: int, body: object
) -> None:
    world.fail("send_code_request", exc)
    resp = api.post(f"{BASE}/send-code", json={"phone": PHONE})
    assert resp.status_code == status
    assert resp.json() == body


def test_api_wrong_code(api: TestClient) -> None:
    api.post(f"{BASE}/send-code", json={"phone": PHONE})
    resp = api.post(f"{BASE}/sign-in", json={"code": "00000"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "telegram_code_invalid"


def test_api_conflicts(api: TestClient, secrets: MemorySecretStore) -> None:
    resp = api.post(f"{BASE}/sign-in", json={"code": "12345"})
    assert (resp.status_code, resp.json()["code"]) == (409, "telegram_login_not_started")
    secrets.set(SecretName.TELEGRAM_SESSION, SESSION_OK)
    resp = api.post(f"{BASE}/send-code", json={"phone": PHONE})
    assert (resp.status_code, resp.json()["code"]) == (409, "telegram_already_logged_in")


def test_api_missing_keys(settings_service: SettingsService, world: FakeTelegramAccount) -> None:
    app = create_app(
        platform_registry=PlatformRegistry(),
        settings_service=settings_service,
        telegram_client_factory=world.factory,
    )
    resp = TestClient(app).post(f"{BASE}/send-code", json={"phone": PHONE})
    assert resp.status_code == 422
    assert resp.json() == {
        "code": "telegram_api_keys_missing",
        "params": {"missing": ["api_id", "api_hash"]},
    }


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("send-code", {"phone": ""}),
        ("send-code", {}),
        ("sign-in", {"code": ""}),
        ("password", {"password": ""}),
    ],
)
def test_api_body_validation(api: TestClient, path: str, body: object) -> None:
    resp = api.post(f"{BASE}/{path}", json=body)
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


def test_api_secret_store_unavailable(
    api: TestClient, secrets: MemorySecretStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_: SecretName) -> str | None:
        raise SecretStoreError("locked")

    monkeypatch.setattr(secrets, "get", broken)
    resp = api.get(BASE)
    assert resp.status_code == 503
    assert resp.json()["code"] == "secret_store_unavailable"
