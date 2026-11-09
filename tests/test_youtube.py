"""YouTube adapter: token refresh, one-request upload, Shorts link, scheduling, deletion.

All HTTP goes through respx mocks.
"""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from video_publisher.domain import Platform, PostFormat, PostText, Publication, PublishTarget, Video
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.youtube import (
    API_BASE,
    MAX_TITLE,
    TOKEN_URL,
    UPLOAD_URL,
    GoogleClient,
    YouTubeAdapter,
    classify,
    error_reasons,
    is_short,
    video_url,
)

SESSION_URL = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=abc"


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).respond(200, json={"access_token": "access-1", "expires_in": 3599})
        yield router


@pytest.fixture
def invalidated() -> list[bool]:
    return []


@pytest.fixture
def adapter(invalidated: list[bool]) -> YouTubeAdapter:
    return YouTubeAdapter(
        client_id="1234567-abc.apps.googleusercontent.com",
        client_secret="GOCSPX-secret-secret-secret",
        refresh_token="refresh-1",
        privacy="unlisted",
        made_for_kids=False,
        category_id="27",
        tags=["cats", "funny cats"],
        google=GoogleClient(sleep=lambda _: None, max_retries=2),
        on_token_invalid=lambda: invalidated.append(True),
    )


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    path = tmp_path / "My clip.mp4"
    path.write_bytes(b"\x01" * 3000)
    return path


def make_pub(
    path: Path, *, title: str = "Hello", description: str = "Text", **video: object
) -> Publication:
    return Publication(
        video=Video(path=path, **video),  # type: ignore[arg-type]
        target=PublishTarget(platform=Platform.YOUTUBE, format=PostFormat.VIDEO),
        text=PostText(title=title, description=description, hashtags=["tag"]),
    )


def mock_upload(api: respx.MockRouter, video_id: str = "vid123") -> tuple[respx.Route, respx.Route]:
    init = api.post(url__startswith=UPLOAD_URL).respond(200, headers={"Location": SESSION_URL})
    put = api.put(SESSION_URL).respond(200, json={"id": video_id, "status": {}})
    return init, put


# --- success ------------------------------------------------------------------------------


def test_publish_sends_metadata_and_whole_file(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    init, put = mock_upload(api)
    result = adapter.publish(make_pub(video_file))

    assert result.external_id == "vid123"
    assert result.url == "https://www.youtube.com/watch?v=vid123"
    sent = init.calls.last.request
    assert "uploadType=resumable" in str(sent.url)
    assert sent.headers["Authorization"] == "Bearer access-1"
    assert sent.headers["X-Upload-Content-Length"] == "3000"
    body = json.loads(sent.content)
    assert body["snippet"] == {
        "title": "Hello",
        "description": "Text\n\n#tag",
        "categoryId": "27",
        "tags": ["cats", "funny cats"],
    }
    assert body["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
    # One PUT with the whole file.
    assert put.call_count == 1
    assert put.calls.last.request.content == b"\x01" * 3000
    assert put.calls.last.request.headers["Content-Length"] == "3000"


def test_refresh_uses_the_stored_token(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    mock_upload(api)
    adapter.publish(make_pub(video_file))
    form = dict(x.split("=", 1) for x in api.calls[0].request.content.decode().split("&"))
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "refresh-1"


def test_short_gets_a_shorts_link(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    mock_upload(api)
    pub = make_pub(video_file, width=1080, height=1920, duration_seconds=59.0)
    assert adapter.publish(pub).url == "https://www.youtube.com/shorts/vid123"


def test_empty_title_falls_back_to_file_name(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    init, _ = mock_upload(api)
    adapter.publish(make_pub(video_file, title="  "))
    assert json.loads(init.calls.last.request.content)["snippet"]["title"] == "My clip"


def test_no_tags_key_without_tags(api: respx.MockRouter, video_file: Path) -> None:
    init, _ = mock_upload(api)
    adapter = YouTubeAdapter(client_id="c", client_secret="s", refresh_token="r")
    adapter.publish(make_pub(video_file))
    body = json.loads(init.calls.last.request.content)
    assert "tags" not in body["snippet"]
    assert body["status"]["privacyStatus"] == "public"


# --- scheduling ---------------------------------------------------------------------------


def test_schedule_uploads_private_with_publish_at(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    init, _ = mock_upload(api)
    at = datetime(2026, 12, 1, 18, 30, tzinfo=timezone(timedelta(hours=3)))
    result = adapter.schedule(make_pub(video_file), at)

    assert result.external_id == "vid123"
    status = json.loads(init.calls.last.request.content)["status"]
    assert status["privacyStatus"] == "private"
    assert status["publishAt"] == "2026-12-01T15:30:00Z"  # converted to UTC


def test_schedule_needs_timezone(adapter: YouTubeAdapter, video_file: Path) -> None:
    with pytest.raises(PublishError) as exc:
        adapter.schedule(make_pub(video_file), datetime(2026, 12, 1, 18, 30))
    assert exc.value.code == "schedule_timezone_required"


def test_native_scheduling_window(adapter: YouTubeAdapter) -> None:
    assert adapter.supports_native_scheduling
    assert adapter.min_schedule_lead == timedelta(minutes=15)
    assert adapter.max_schedule_ahead == timedelta(days=365)


def test_cancel_deletes_the_video(api: respx.MockRouter, adapter: YouTubeAdapter) -> None:
    route = api.delete(f"{API_BASE}/videos", params={"id": "vid123"}).respond(204)
    adapter.cancel_scheduled("vid123")
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "Bearer access-1"


def test_cancel_ignores_already_deleted(api: respx.MockRouter, adapter: YouTubeAdapter) -> None:
    api.delete(f"{API_BASE}/videos").respond(
        404, json={"error": {"code": 404, "errors": [{"reason": "videoNotFound"}]}}
    )
    adapter.cancel_scheduled("vid123")  # no error


def test_cancel_reports_other_errors(api: respx.MockRouter, adapter: YouTubeAdapter) -> None:
    api.delete(f"{API_BASE}/videos").respond(
        403, json={"error": {"code": 403, "errors": [{"reason": "forbidden"}]}}
    )
    with pytest.raises(PublishError) as exc:
        adapter.cancel_scheduled("vid123")
    assert exc.value.code == "youtube_permission_denied"


# --- validation ---------------------------------------------------------------------------


def test_validate_ok(adapter: YouTubeAdapter, video_file: Path) -> None:
    assert adapter.validate(Video(path=video_file)).ok


def test_validate_missing_and_format(adapter: YouTubeAdapter, tmp_path: Path) -> None:
    assert adapter.validate(Video(path=tmp_path / "x.mp4")).errors == ["video_not_found"]
    mov = tmp_path / "x.mov"
    mov.write_bytes(b"1")
    assert adapter.validate(Video(path=mov)).errors == ["youtube_unsupported_format"]


def test_validate_too_large(
    adapter: YouTubeAdapter, video_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("video_publisher.platforms.youtube.MAX_UPLOAD_BYTES", 10)
    assert adapter.validate(Video(path=video_file)).errors == ["youtube_file_too_large"]


def test_title_too_long(adapter: YouTubeAdapter, video_file: Path) -> None:
    with pytest.raises(PublishError) as exc:
        adapter.check_text(make_pub(video_file, title="x" * (MAX_TITLE + 1)))
    assert exc.value.code == "youtube_title_too_long"
    assert exc.value.params == {"max": 100, "length": 101}


def test_description_limit_counts_bytes(adapter: YouTubeAdapter, video_file: Path) -> None:
    # 2600 Cyrillic letters are 5200 bytes: over YouTube's 5000-byte limit.
    with pytest.raises(PublishError) as exc:
        adapter.check_text(make_pub(video_file, description="я" * 2600))
    assert exc.value.code == "youtube_description_too_long"


def test_angle_brackets_rejected(adapter: YouTubeAdapter, video_file: Path) -> None:
    with pytest.raises(PublishError) as exc:
        adapter.check_text(make_pub(video_file, title="a <b>"))
    assert exc.value.code == "youtube_angle_brackets"


def test_publish_checks_before_uploading(
    api: respx.MockRouter, adapter: YouTubeAdapter, tmp_path: Path
) -> None:
    init, _ = mock_upload(api)
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(tmp_path / "gone.mp4"))
    assert exc.value.code == "video_not_found"
    assert not init.called


# --- token problems -----------------------------------------------------------------------


def test_revoked_login_is_forgotten(
    adapter: YouTubeAdapter, invalidated: list[bool], video_file: Path
) -> None:
    with respx.mock() as api:
        api.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})
        with pytest.raises(PublishError) as exc:
            adapter.publish(make_pub(video_file))
    assert exc.value.code == "youtube_token_invalid"
    assert invalidated == [True]


def test_wrong_client_keys(adapter: YouTubeAdapter, invalidated: list[bool]) -> None:
    with respx.mock() as api:
        api.post(TOKEN_URL).respond(401, json={"error": "invalid_client"})
        with pytest.raises(PublishError) as exc:
            adapter.authenticate()
    assert exc.value.code == "youtube_client_invalid"
    assert invalidated == []  # the login itself may be fine


def test_token_endpoint_down_is_retried(adapter: YouTubeAdapter) -> None:
    with respx.mock() as api:
        route = api.post(TOKEN_URL).mock(
            side_effect=[
                httpx.ConnectError("down"),
                httpx.Response(200, json={"access_token": "a"}),
            ]
        )
        adapter.authenticate()
    assert route.call_count == 2


def test_token_endpoint_5xx(adapter: YouTubeAdapter) -> None:
    with respx.mock() as api:
        api.post(TOKEN_URL).respond(503)
        with pytest.raises(PublishError) as exc:
            adapter.authenticate()
    assert exc.value.code == "youtube_unavailable"


def test_token_endpoint_other_refusal(adapter: YouTubeAdapter) -> None:
    with respx.mock() as api:
        api.post(TOKEN_URL).respond(400, json={"error": "invalid_request"})
        with pytest.raises(PublishError) as exc:
            adapter.authenticate()
    assert exc.value.code == "youtube_rejected"


def test_no_network(adapter: YouTubeAdapter) -> None:
    with respx.mock() as api:
        api.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("offline"))
        with pytest.raises(PublishError) as exc:
            adapter.authenticate()
    assert exc.value.code == "network_error"
    assert exc.value.retryable


# --- API refusals -------------------------------------------------------------------------


def _err(status: int, reason: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": status, "message": "m", "errors": [{"reason": reason}]}}
    )


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (_err(403, "quotaExceeded"), "youtube_quota_exceeded"),
        (_err(400, "uploadLimitExceeded"), "youtube_upload_limit"),
        (_err(401, "youtubeSignupRequired"), "youtube_token_invalid"),
        (_err(403, "youtubeSignupRequired"), "youtube_no_channel"),
        (_err(403, "accessNotConfigured"), "youtube_api_disabled"),
        (_err(403, "forbidden"), "youtube_permission_denied"),
        (_err(400, "invalidCategoryId"), "youtube_rejected"),
    ],
)
def test_upload_session_refusals(
    api: respx.MockRouter,
    adapter: YouTubeAdapter,
    video_file: Path,
    response: httpx.Response,
    code: str,
) -> None:
    api.post(url__startswith=UPLOAD_URL).mock(return_value=response)
    put = api.put(SESSION_URL).respond(200, json={"id": "x"})
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == code
    assert not put.called


def test_rate_limit_on_session_is_retried(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    init = api.post(url__startswith=UPLOAD_URL).mock(
        side_effect=[
            _err(403, "rateLimitExceeded"),
            httpx.Response(503),
            httpx.Response(200, headers={"Location": SESSION_URL}),
        ]
    )
    api.put(SESSION_URL).respond(200, json={"id": "vid9"})
    assert adapter.publish(make_pub(video_file)).external_id == "vid9"
    assert init.call_count == 3


def test_session_without_location(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    api.post(url__startswith=UPLOAD_URL).respond(200)
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "youtube_rejected"


def test_session_timeout_is_retried(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    # Creating the session posts nothing yet, so repeating it is safe.
    api.post(url__startswith=UPLOAD_URL).mock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.Response(200, headers={"Location": SESSION_URL}),
        ]
    )
    api.put(SESSION_URL).respond(200, json={"id": "vid9"})
    assert adapter.publish(make_pub(video_file)).external_id == "vid9"


# --- the file upload (no retry once bytes are sent) ---------------------------------------


@pytest.mark.parametrize(
    "failure",
    [httpx.ReadTimeout("slow"), httpx.WriteError("broken"), httpx.RemoteProtocolError("x")],
)
def test_drop_during_upload_is_uncertain_and_not_retried(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path, failure: Exception
) -> None:
    api.post(url__startswith=UPLOAD_URL).respond(200, headers={"Location": SESSION_URL})
    put = api.put(SESSION_URL).mock(side_effect=failure)
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "youtube_timeout_uncertain"
    assert put.call_count == 1


def test_gateway_error_during_upload_is_uncertain(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    api.post(url__startswith=UPLOAD_URL).respond(200, headers={"Location": SESSION_URL})
    put = api.put(SESSION_URL).respond(502)
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "youtube_timeout_uncertain"
    assert put.call_count == 1


def test_connect_failure_before_upload_is_retried(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    api.post(url__startswith=UPLOAD_URL).respond(200, headers={"Location": SESSION_URL})
    put = api.put(SESSION_URL).mock(
        side_effect=[httpx.ConnectError("no"), httpx.Response(200, json={"id": "vid9"})]
    )
    assert adapter.publish(make_pub(video_file)).external_id == "vid9"
    assert put.call_count == 2


def test_connect_failure_gives_up(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    api.post(url__startswith=UPLOAD_URL).respond(200, headers={"Location": SESSION_URL})
    api.put(SESSION_URL).mock(side_effect=httpx.ConnectError("no"))
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "network_error"


def test_upload_refused_by_youtube(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    api.post(url__startswith=UPLOAD_URL).respond(200, headers={"Location": SESSION_URL})
    api.put(SESSION_URL).mock(return_value=_err(400, "invalidVideoMetadata"))
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    assert exc.value.code == "youtube_rejected"
    assert exc.value.params == {"status": 400, "reason": "invalidVideoMetadata"}


def test_tokens_never_in_errors(
    api: respx.MockRouter, adapter: YouTubeAdapter, video_file: Path
) -> None:
    api.post(url__startswith=UPLOAD_URL).mock(return_value=_err(403, "forbidden"))
    with pytest.raises(PublishError) as exc:
        adapter.publish(make_pub(video_file))
    text = repr(exc.value) + str(exc.value.params)
    for secret in ("access-1", "refresh-1", "GOCSPX"):
        assert secret not in text


# --- helpers ------------------------------------------------------------------------------


def test_error_reasons_both_formats() -> None:
    body = {"error": {"errors": [{"reason": "a"}], "details": [{"reason": "B"}, {"x": 1}]}}
    assert error_reasons(body) == ["a", "B"]
    assert error_reasons(None) == []
    assert error_reasons({"error": "text"}) == []


def test_classify_fallbacks() -> None:
    assert classify(429, []) == ("youtube_rate_limited", True)
    assert classify(500, []) == ("youtube_unavailable", True)
    assert classify(404, []) == ("youtube_video_not_found", False)
    assert classify(403, ["SERVICE_DISABLED"]) == ("youtube_api_disabled", False)


def test_is_short_and_url(tmp_path: Path) -> None:
    p = tmp_path / "a.mp4"
    assert is_short(Video(path=p, width=1080, height=1920, duration_seconds=180))
    assert not is_short(Video(path=p, width=1080, height=1920, duration_seconds=181))
    assert not is_short(Video(path=p, width=1920, height=1080, duration_seconds=10))
    assert not is_short(Video(path=p))
    assert video_url("z", short=True).endswith("/shorts/z")
