import pytest
from fastapi.testclient import TestClient

from video_publisher.auth.secret_store import MemorySecretStore, SecretName, SecretStoreError

VALID_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
VALID_HASH = "0123456789abcdef0123456789abcdef"

NO_SECRETS = {
    "telegram_bot_token": False,
    "telegram_api_hash": False,
    "telegram_session": False,
    "facebook_app_secret": False,
    "facebook_page_token": False,
}


def test_get_defaults(client: TestClient) -> None:
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json() == {
        "language": "ru",
        "telegram": {"mode": "bot", "channel": None, "api_id": None},
        "facebook": {"app_id": None, "page_id": None, "page_name": None},
        "secrets": NO_SECRETS,
    }


def test_patch_language_persists(client: TestClient) -> None:
    resp = client.patch("/api/settings", json={"language": "en"})
    assert resp.status_code == 200
    assert resp.json()["language"] == "en"
    assert client.get("/api/settings").json()["language"] == "en"


def test_patch_telegram_partial(client: TestClient) -> None:
    client.patch("/api/settings", json={"telegram": {"channel": "@my_channel"}})
    resp = client.patch("/api/settings", json={"telegram": {"mode": "account", "api_id": 42}})
    assert resp.json()["telegram"] == {"mode": "account", "channel": "@my_channel", "api_id": 42}


@pytest.mark.parametrize(
    ("body", "field", "code"),
    [
        ({"language": "de"}, "body.language", "literal_error"),
        ({"telegram": {"channel": "no-at-sign"}}, "body.telegram.channel", "invalid_channel"),
        ({"telegram": {"api_id": 0}}, "body.telegram.api_id", "greater_than"),
        ({"telegram": {"api_id": "abc"}}, "body.telegram.api_id", "int_parsing"),
        ({"telegram": {"mode": "pigeon"}}, "body.telegram.mode", "enum"),
    ],
)
def test_patch_validation_errors(client: TestClient, body: object, field: str, code: str) -> None:
    resp = client.patch("/api/settings", json=body)
    assert resp.status_code == 422
    assert resp.json() == {"code": "validation_error", "params": {"fields": {field: code}}}
    assert client.get("/api/settings").json()["language"] == "ru"  # nothing changed


def test_patch_null_mode_is_validation_error(client: TestClient) -> None:
    resp = client.patch("/api/settings", json={"telegram": {"mode": None}})
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


def test_patch_rejects_non_json(client: TestClient) -> None:
    resp = client.patch(
        "/api/settings", content="nope", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 422


def test_set_secret_is_write_only(client: TestClient, secrets: MemorySecretStore) -> None:
    resp = client.put("/api/secrets/telegram_bot_token", json={"value": VALID_TOKEN})
    assert resp.status_code == 204
    assert secrets.get(SecretName.TELEGRAM_BOT_TOKEN) == VALID_TOKEN
    body = client.get("/api/settings").json()
    assert body["secrets"]["telegram_bot_token"] is True
    assert VALID_TOKEN not in client.get("/api/settings").text


def test_set_api_hash(client: TestClient) -> None:
    assert (
        client.put("/api/secrets/telegram_api_hash", json={"value": VALID_HASH}).status_code == 204
    )
    assert client.get("/api/settings").json()["secrets"]["telegram_api_hash"] is True


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("telegram_bot_token", "garbage", "invalid_bot_token"),
        ("telegram_api_hash", "garbage", "invalid_api_hash"),
    ],
)
def test_set_secret_invalid_format(client: TestClient, name: str, value: str, code: str) -> None:
    resp = client.put(f"/api/secrets/{name}", json={"value": value})
    assert resp.status_code == 422
    assert resp.json() == {"code": code, "params": {}}


def test_set_secret_empty_value(client: TestClient) -> None:
    resp = client.put("/api/secrets/telegram_bot_token", json={"value": ""})
    assert resp.status_code == 422
    assert resp.json()["params"]["fields"] == {"body.value": "string_too_short"}


def test_session_secret_cannot_be_set_by_hand(client: TestClient) -> None:
    resp = client.put("/api/secrets/telegram_session", json={"value": "x"})
    assert resp.status_code == 422


def test_unknown_secret_name(client: TestClient) -> None:
    assert client.put("/api/secrets/nope", json={"value": "x"}).status_code == 422
    assert client.delete("/api/secrets/nope").status_code == 422


def test_delete_secret(client: TestClient, secrets: MemorySecretStore) -> None:
    secrets.set(SecretName.TELEGRAM_SESSION, "session")
    assert client.delete("/api/secrets/telegram_session").status_code == 204
    assert secrets.get(SecretName.TELEGRAM_SESSION) is None
    assert client.delete("/api/secrets/telegram_session").status_code == 204  # idempotent


class FailingStore(MemorySecretStore):
    def get(self, name: SecretName) -> str | None:
        raise SecretStoreError("locked")

    def set(self, name: SecretName, value: str) -> None:
        raise SecretStoreError("locked")

    def delete(self, name: SecretName) -> None:
        raise SecretStoreError("locked")


@pytest.fixture
def failing_client(client: TestClient) -> TestClient:
    service = client.app.state.settings_service  # type: ignore[attr-defined]
    service._secrets = FailingStore()
    return client


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("GET", "/api/settings", None),
        ("PATCH", "/api/settings", {"language": "en"}),
        ("PUT", "/api/secrets/telegram_bot_token", {"value": VALID_TOKEN}),
        ("DELETE", "/api/secrets/telegram_bot_token", None),
    ],
)
def test_secret_store_unavailable(
    failing_client: TestClient, method: str, url: str, body: object
) -> None:
    resp = failing_client.request(method, url, json=body)
    assert resp.status_code == 503
    assert resp.json() == {"code": "secret_store_unavailable", "params": {}}
