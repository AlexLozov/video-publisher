from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from video_publisher.domain import Platform, PostFormat, PostText, Publication, PublishTarget, Video
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.facebook_page import (
    MAX_TITLE,
    MAX_UPLOAD_BYTES,
    FacebookPageAdapter,
    GraphClient,
    build_description,
    classify,
    video_url,
)

PAGE = "1234567890"
TOKEN = "EAAPageToken"
GRAPH = "https://graph.facebook.com/v24.0"
VIDEO = "https://graph-video.facebook.com/v24.0"


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def invalidated() -> list[bool]:
    return []


@pytest.fixture
def adapter(sleeps: list[float], invalidated: list[bool]) -> FacebookPageAdapter:
    graph = GraphClient(sleep=sleeps.append, max_retries=2)
    return FacebookPageAdapter(
        PAGE, TOKEN, graph=graph, on_token_invalid=lambda: invalidated.append(True)
    )


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 1024)
    return path


def make_pub(path: Path, text: PostText | None = None) -> Publication:
    return Publication(
        video=Video(path=path),
        target=PublishTarget(platform=Platform.FACEBOOK, format=PostFormat.VIDEO),
        text=text or PostText(),
    )


def graph_err(status: int, code: int, subcode: int | None = None) -> httpx.Response:
    err: dict[str, object] = {"message": "x", "type": "OAuthException", "code": code}
    if subcode is not None:
        err["error_subcode"] = subcode
    return httpx.Response(status, json={"error": err})


def form(request: httpx.Request) -> bytes:
    return request.read()


# --- helpers -------------------------------------------------------------------------


def test_description_with_hashtags() -> None:
    text = PostText(title="T", description=" Body ", hashtags=["cats", "#dogs", " "])
    assert build_description(text) == "Body\n\n#cats #dogs"


def test_description_empty() -> None:
    assert build_description(PostText(title="only title")) == ""


def test_video_url() -> None:
    assert video_url(PAGE, "42") == f"https://www.facebook.com/{PAGE}/videos/42"


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (400, 190, ("facebook_token_invalid", False)),
        (401, None, ("facebook_token_invalid", False)),
        (400, 4, ("facebook_rate_limited", True)),
        (400, 32, ("facebook_rate_limited", True)),
        (429, None, ("facebook_rate_limited", True)),
        (500, 2, ("facebook_unavailable", True)),
        (503, None, ("facebook_unavailable", True)),
        (403, 200, ("facebook_permission_denied", False)),
        (400, 10, ("facebook_permission_denied", False)),
        (400, 352, ("facebook_video_rejected", False)),
        (404, 100, ("facebook_page_not_found", False)),
        (400, 100, ("facebook_rejected", False)),
    ],
)
def test_classify(status: int, code: int | None, expected: tuple[str, bool]) -> None:
    assert classify(status, code) == expected


# --- validate ------------------------------------------------------------------------


def test_validate_ok(adapter: FacebookPageAdapter, video_file: Path) -> None:
    assert adapter.validate(Video(path=video_file)).ok


def test_validate_missing_file(adapter: FacebookPageAdapter, tmp_path: Path) -> None:
    result = adapter.validate(Video(path=tmp_path / "nope.mp4"))
    assert result.errors == ["video_not_found"]


def test_validate_wrong_format(adapter: FacebookPageAdapter, tmp_path: Path) -> None:
    path = tmp_path / "clip.mov"
    path.write_bytes(b"x")
    assert adapter.validate(Video(path=path)).errors == ["facebook_unsupported_format"]


def test_validate_too_large(adapter: FacebookPageAdapter, tmp_path: Path) -> None:
    path = tmp_path / "big.mp4"
    with path.open("wb") as fh:
        fh.truncate(MAX_UPLOAD_BYTES + 1)  # sparse file, no real disk use
    assert adapter.validate(Video(path=path)).errors == ["facebook_file_too_large"]


def test_publish_too_large_has_sizes(adapter: FacebookPageAdapter, tmp_path: Path) -> None:
    path = tmp_path / "big.mp4"
    with path.open("wb") as fh:
        fh.truncate(MAX_UPLOAD_BYTES + 1)
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(path))
    assert exc.value.code == "facebook_file_too_large"
    assert exc.value.params["max_mb"] == 1024


def test_title_too_long(adapter: FacebookPageAdapter, video_file: Path) -> None:
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file, PostText(title="x" * (MAX_TITLE + 1))))
    assert exc.value.code == "facebook_title_too_long"


# --- publish -------------------------------------------------------------------------


def test_publish_success(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path
) -> None:
    route = api.post(f"{VIDEO}/{PAGE}/videos").respond(200, json={"id": "777"})
    result = adapter.publish(
        make_pub(video_file, PostText(title="Hello", description="Desc", hashtags=["a"]))
    )
    assert result.external_id == "777"
    assert result.url == video_url(PAGE, "777")
    body = form(route.calls.last.request)
    assert b'name="title"' in body and b"Hello" in body
    assert b"Desc\r\n\r\n#a" in body or b"Desc\n\n#a" in body
    assert b'name="source"; filename="clip.mp4"' in body
    assert b"scheduled_publish_time" not in body
    assert TOKEN.encode() in body  # token in the form, not in the URL
    assert TOKEN not in str(route.calls.last.request.url)


def test_publish_without_text_sends_no_title(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path
) -> None:
    route = api.post(f"{VIDEO}/{PAGE}/videos").respond(200, json={"id": "1"})
    adapter.publish(make_pub(video_file))
    body = form(route.calls.last.request)
    assert b'name="title"' not in body and b'name="description"' not in body


def test_publish_missing_id(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path
) -> None:
    api.post(f"{VIDEO}/{PAGE}/videos").respond(200, json={})
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "facebook_rejected"


def test_publish_expired_token_forgets_it(
    api: respx.MockRouter,
    adapter: FacebookPageAdapter,
    video_file: Path,
    invalidated: list[bool],
) -> None:
    api.post(f"{VIDEO}/{PAGE}/videos").mock(return_value=graph_err(400, 190, 463))
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "facebook_token_invalid"
    assert exc.value.params["fb_subcode"] == 463
    assert invalidated == [True]


def test_publish_permission_denied(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path
) -> None:
    api.post(f"{VIDEO}/{PAGE}/videos").mock(return_value=graph_err(403, 200))
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "facebook_permission_denied"


def test_publish_rate_limited_is_not_retried(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path
) -> None:
    route = api.post(f"{VIDEO}/{PAGE}/videos").mock(return_value=graph_err(400, 32))
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "facebook_rate_limited"
    assert exc.value.retryable
    assert route.call_count == 1


def test_publish_connect_error_is_retried(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path, sleeps: list[float]
) -> None:
    route = api.post(f"{VIDEO}/{PAGE}/videos")
    route.side_effect = [httpx.ConnectError("down"), httpx.Response(200, json={"id": "5"})]
    assert adapter.publish(make_pub(video_file)).external_id == "5"
    assert sleeps == [1.0]


def test_publish_no_network(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path
) -> None:
    route = api.post(f"{VIDEO}/{PAGE}/videos").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "network_error"
    assert route.call_count == 3  # first try + 2 retries


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("slow"), httpx.WriteTimeout("slow"), httpx.RemoteProtocolError("x")]
)
def test_publish_drop_during_upload_is_not_retried(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path, error: Exception
) -> None:
    route = api.post(f"{VIDEO}/{PAGE}/videos").mock(side_effect=error)
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "facebook_timeout_uncertain"
    assert route.call_count == 1


def test_publish_gateway_error_is_uncertain(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path
) -> None:
    route = api.post(f"{VIDEO}/{PAGE}/videos").respond(502, text="<html>Bad gateway</html>")
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "facebook_timeout_uncertain"
    assert route.call_count == 1


def test_publish_graph_server_error_is_unavailable(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path
) -> None:
    api.post(f"{VIDEO}/{PAGE}/videos").mock(return_value=graph_err(500, 2))
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "facebook_unavailable"


# --- schedule ------------------------------------------------------------------------


def test_schedule_sends_unpublished_with_time(
    api: respx.MockRouter, adapter: FacebookPageAdapter, video_file: Path
) -> None:
    route = api.post(f"{VIDEO}/{PAGE}/videos").respond(200, json={"id": "9"})
    at = datetime(2030, 1, 2, 3, 4, tzinfo=UTC)
    result = adapter.schedule(make_pub(video_file), at)
    assert result.external_id == "9"
    body = form(route.calls.last.request)
    assert b"false" in body
    assert str(int(at.timestamp())).encode() in body


def test_schedule_naive_time_rejected(adapter: FacebookPageAdapter, video_file: Path) -> None:
    with pytest.raises(PublishError) as exc:
        adapter.schedule(make_pub(video_file), datetime(2030, 1, 1))
    assert exc.value.code == "schedule_timezone_required"


def test_scheduling_window() -> None:
    assert FacebookPageAdapter.min_schedule_lead.total_seconds() == 600
    assert FacebookPageAdapter.max_schedule_ahead.days == 30
    assert FacebookPageAdapter(PAGE, TOKEN).supports_native_scheduling


# --- cancel / authenticate -------------------------------------------------------------


def test_cancel_deletes_video(api: respx.MockRouter, adapter: FacebookPageAdapter) -> None:
    route = api.delete(f"{GRAPH}/9").respond(200, json={"success": True})
    adapter.cancel_scheduled("9")
    assert route.called
    assert route.calls.last.request.url.params["access_token"] == TOKEN


def test_cancel_already_gone_is_fine(api: respx.MockRouter, adapter: FacebookPageAdapter) -> None:
    api.delete(f"{GRAPH}/9").mock(return_value=graph_err(404, 100, 33))
    adapter.cancel_scheduled("9")


def test_cancel_retries_transient(
    api: respx.MockRouter, adapter: FacebookPageAdapter, sleeps: list[float]
) -> None:
    route = api.delete(f"{GRAPH}/9")
    route.side_effect = [graph_err(500, 2), httpx.Response(200, json={"success": True})]
    adapter.cancel_scheduled("9")
    assert route.call_count == 2


def test_cancel_denied(api: respx.MockRouter, adapter: FacebookPageAdapter) -> None:
    api.delete(f"{GRAPH}/9").mock(return_value=graph_err(403, 200))
    with pytest.raises(PublishError) as exc:
        adapter.cancel_scheduled("9")
    assert exc.value.code == "facebook_permission_denied"


def test_authenticate_ok(api: respx.MockRouter, adapter: FacebookPageAdapter) -> None:
    api.get(f"{GRAPH}/{PAGE}").respond(200, json={"id": PAGE})
    adapter.authenticate()


def test_authenticate_get_timeout_retried_then_fails(
    api: respx.MockRouter, adapter: FacebookPageAdapter, sleeps: list[float]
) -> None:
    route = api.get(f"{GRAPH}/{PAGE}").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PublishError) as exc:
        adapter.authenticate()
    assert exc.value.code == "network_error"
    assert route.call_count == 3
    assert sleeps == [1.0, 2.0]


def test_delete_timeout_not_retried(api: respx.MockRouter, adapter: FacebookPageAdapter) -> None:
    route = api.delete(f"{GRAPH}/9").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PublishError):
        adapter.cancel_scheduled("9")
    assert route.call_count == 1


def test_request_transport_error(api: respx.MockRouter, adapter: FacebookPageAdapter) -> None:
    api.get(f"{GRAPH}/{PAGE}").mock(side_effect=httpx.RemoteProtocolError("x"))
    with pytest.raises(PublishError) as exc:
        adapter.authenticate()
    assert exc.value.code == "network_error"


def test_request_connect_error_gives_up(
    api: respx.MockRouter, adapter: FacebookPageAdapter
) -> None:
    route = api.get(f"{GRAPH}/{PAGE}").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(PublishError):
        adapter.authenticate()
    assert route.call_count == 3


def test_token_never_logged(
    api: respx.MockRouter,
    adapter: FacebookPageAdapter,
    video_file: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    api.post(f"{VIDEO}/{PAGE}/videos").respond(200, json={"id": "1"})
    api.get(f"{GRAPH}/{PAGE}").respond(200, json={"id": PAGE})
    adapter.publish(make_pub(video_file))
    adapter.authenticate()
    assert TOKEN not in caplog.text
