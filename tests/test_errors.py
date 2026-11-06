from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_publisher.api.errors import ApiError, install_error_handlers


def make_client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api-error")
    def api_error() -> None:
        raise ApiError("video_too_long", status_code=422, max_seconds=60)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("секретная деталь")

    @app.get("/typed/{n}")
    def typed(n: int) -> int:
        return n

    return TestClient(app, raise_server_exceptions=False)


def test_api_error_returns_code_and_params() -> None:
    resp = make_client().get("/api-error")
    assert resp.status_code == 422
    assert resp.json() == {"code": "video_too_long", "params": {"max_seconds": 60}}


def test_validation_error_returns_code_and_fields() -> None:
    resp = make_client().get("/typed/abc")
    assert resp.status_code == 422
    assert resp.json() == {
        "code": "validation_error",
        "params": {"fields": {"path.n": "int_parsing"}},
    }


def test_method_not_allowed_returns_code() -> None:
    resp = make_client().post("/typed/1")
    assert resp.status_code == 405
    assert resp.json()["code"] == "method_not_allowed"


def test_unknown_http_status_falls_back_to_generic_code() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/teapot")
    def teapot() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=418, detail="I'm a teapot")

    resp = TestClient(app).get("/teapot")
    assert resp.status_code == 418
    assert resp.json() == {"code": "http_error", "params": {}}


def test_unexpected_exception_hides_details() -> None:
    resp = make_client().get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {"code": "internal_error", "params": {}}
    assert "секретная" not in resp.text
