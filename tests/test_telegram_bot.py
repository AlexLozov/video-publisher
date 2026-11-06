import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from video_publisher.domain import Platform, PostFormat, PostText, Publication, PublishTarget, Video
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.telegram_bot import (
    MAX_UPLOAD_BYTES,
    TelegramBotAdapter,
    build_caption,
    post_url,
)

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
BASE = f"https://api.telegram.org/bot{TOKEN}"
CHANNEL = "@my_channel"


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def adapter(sleeps: list[float]) -> TelegramBotAdapter:
    return TelegramBotAdapter(TOKEN, CHANNEL, sleep=sleeps.append, max_retries=3)


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 1024)
    return path


def make_publication(path: Path, text: PostText | None = None, **video: object) -> Publication:
    return Publication(
        video=Video(path=path, **video),  # type: ignore[arg-type]
        target=PublishTarget(platform=Platform.TELEGRAM, format=PostFormat.VIDEO),
        text=text or PostText(),
    )


def ok(result: object) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def fail(status: int, description: str, **extra: object) -> httpx.Response:
    return httpx.Response(
        status, json={"ok": False, "error_code": status, "description": description, **extra}
    )


# --- helpers -------------------------------------------------------------------------


def test_caption_joins_parts_and_normalizes_hashtags() -> None:
    text = PostText(title=" Title ", description="Body", hashtags=["cats", "#dogs", " ", ""])
    assert build_caption(text) == "Title\n\nBody\n\n#cats #dogs"


def test_caption_empty() -> None:
    assert build_caption(PostText()) == ""


def test_caption_only_hashtags() -> None:
    assert build_caption(PostText(hashtags=["a"])) == "#a"


@pytest.mark.parametrize(
    ("channel", "url"),
    [
        ("@my_channel", "https://t.me/my_channel/42"),
        ("-1001234567890", "https://t.me/c/1234567890/42"),
        ("12345", None),
    ],
)
def test_post_url(channel: str, url: str | None) -> None:
    assert post_url(channel, 42) == url


def test_no_native_scheduling(adapter: TelegramBotAdapter, video_file: Path) -> None:
    assert adapter.supports_native_scheduling is False
    with pytest.raises(PublishError) as info:
        adapter.schedule(make_publication(video_file), datetime.now(UTC))
    assert info.value.code == "telegram_bot_no_native_scheduling"


# --- validate ----------------------------------------------------------------------


def test_validate_ok(adapter: TelegramBotAdapter, video_file: Path) -> None:
    assert adapter.validate(Video(path=video_file)).ok


def test_validate_missing_file(adapter: TelegramBotAdapter, tmp_path: Path) -> None:
    result = adapter.validate(Video(path=tmp_path / "nope.mp4"))
    assert result.errors == ["video_not_found"]


def test_validate_directory_is_not_a_video(adapter: TelegramBotAdapter, tmp_path: Path) -> None:
    assert adapter.validate(Video(path=tmp_path)).errors == ["video_not_found"]


@pytest.mark.parametrize("name", ["clip.mov", "clip.mkv", "clip.avi", "clip"])
def test_validate_unsupported_format(
    adapter: TelegramBotAdapter, tmp_path: Path, name: str
) -> None:
    path = tmp_path / name
    path.write_bytes(b"x")
    assert adapter.validate(Video(path=path)).errors == ["telegram_unsupported_format"]


def test_validate_uppercase_extension_ok(adapter: TelegramBotAdapter, tmp_path: Path) -> None:
    path = tmp_path / "CLIP.MP4"
    path.write_bytes(b"x")
    assert adapter.validate(Video(path=path)).ok


def _sized_file(tmp_path: Path, size: int) -> Path:
    path = tmp_path / "big.mp4"
    with path.open("wb") as fh:
        fh.truncate(size)  # sparse file, no real disk usage
    return path


def test_validate_exactly_50mb_is_ok(adapter: TelegramBotAdapter, tmp_path: Path) -> None:
    assert adapter.validate(Video(path=_sized_file(tmp_path, MAX_UPLOAD_BYTES))).ok


def test_validate_over_50mb(adapter: TelegramBotAdapter, tmp_path: Path) -> None:
    result = adapter.validate(Video(path=_sized_file(tmp_path, MAX_UPLOAD_BYTES + 1)))
    assert result.errors == ["telegram_bot_file_too_large"]


# --- publish -----------------------------------------------------------------------


def test_publish_sends_video(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path
) -> None:
    route = api.post("/sendVideo").mock(return_value=ok({"message_id": 42}))
    pub = make_publication(
        video_file,
        PostText(title="Hello", hashtags=["test"]),
        duration_seconds=12.6,
        width=1080,
        height=1920,
    )

    result = adapter.publish(pub)

    assert result.external_id == "42"
    assert result.url == "https://t.me/my_channel/42"
    request = route.calls.last.request
    body = request.content.decode("latin-1")
    for field, value in [
        ("chat_id", CHANNEL),
        ("caption", "Hello\n\n#test"),
        ("supports_streaming", "true"),
        ("duration", "13"),
        ("width", "1080"),
        ("height", "1920"),
    ]:
        assert f'name="{field}"\r\n\r\n{value}\r\n' in body
    assert 'name="video"; filename="clip.mp4"' in body
    assert request.headers["content-type"].startswith("multipart/form-data")


def test_publish_without_caption_or_metadata(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path
) -> None:
    route = api.post("/sendVideo").mock(return_value=ok({"message_id": 1}))
    adapter.publish(make_publication(video_file))
    body = route.calls.last.request.content.decode("latin-1")
    assert 'name="caption"' not in body
    assert 'name="duration"' not in body
    assert 'name="width"' not in body


def test_publish_too_large_has_hint(adapter: TelegramBotAdapter, tmp_path: Path) -> None:
    path = _sized_file(tmp_path, 60 * 1024 * 1024)
    with pytest.raises(PublishError) as info:
        adapter.publish(make_publication(path))
    assert info.value.code == "telegram_bot_file_too_large"
    assert info.value.params == {"max_mb": 50, "size_mb": 60.0, "hint": "switch_to_account"}


def test_publish_missing_file(adapter: TelegramBotAdapter, tmp_path: Path) -> None:
    with pytest.raises(PublishError) as info:
        adapter.publish(make_publication(tmp_path / "gone.mp4"))
    assert info.value.code == "video_not_found"


def test_publish_caption_too_long(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path
) -> None:
    route = api.post("/sendVideo")
    with pytest.raises(PublishError) as info:
        adapter.publish(make_publication(video_file, PostText(description="x" * 1025)))
    assert info.value.code == "caption_too_long"
    assert info.value.params == {"max": 1024, "length": 1025}
    assert not route.called


def test_publish_caption_exactly_at_limit(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path
) -> None:
    api.post("/sendVideo").mock(return_value=ok({"message_id": 1}))
    adapter.publish(make_publication(video_file, PostText(description="я" * 1024)))


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (fail(401, "Unauthorized"), "telegram_invalid_token"),
        (fail(400, "Bad Request: chat not found"), "telegram_chat_not_found"),
        (fail(403, "Forbidden: bot is not a member of the channel chat"), "telegram_bot_not_admin"),
        (fail(400, "Bad Request: not enough rights to send videos"), "telegram_bot_not_admin"),
        (fail(413, "Request Entity Too Large"), "telegram_bot_file_too_large"),
        (fail(400, "Bad Request: wrong file identifier"), "telegram_rejected"),
        (httpx.Response(400, text="<html>bad</html>"), "telegram_rejected"),
    ],
)
def test_publish_maps_api_errors_without_retry(
    adapter: TelegramBotAdapter,
    api: respx.MockRouter,
    video_file: Path,
    sleeps: list[float],
    response: httpx.Response,
    code: str,
) -> None:
    route = api.post("/sendVideo").mock(return_value=response)
    with pytest.raises(PublishError) as info:
        adapter.publish(make_publication(video_file))
    assert info.value.code == code
    assert info.value.retryable is False
    assert route.call_count == 1
    assert sleeps == []


def test_rate_limit_waits_retry_after_then_succeeds(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path, sleeps: list[float]
) -> None:
    route = api.post("/sendVideo").mock(
        side_effect=[
            fail(429, "Too Many Requests: retry after 7", parameters={"retry_after": 7}),
            ok({"message_id": 5}),
        ]
    )
    assert adapter.publish(make_publication(video_file)).external_id == "5"
    assert sleeps == [7]
    assert route.call_count == 2


def test_rate_limit_gives_up_after_max_retries(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path, sleeps: list[float]
) -> None:
    api.post("/sendVideo").mock(
        return_value=fail(429, "Too Many Requests", parameters={"retry_after": 3})
    )
    with pytest.raises(PublishError) as info:
        adapter.publish(make_publication(video_file))
    assert info.value.code == "telegram_rate_limited"
    assert info.value.retryable is True
    assert info.value.params == {"retry_after": 3}
    assert sleeps == [3, 3, 3]


def test_server_errors_retry_with_backoff(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path, sleeps: list[float]
) -> None:
    api.post("/sendVideo").mock(
        side_effect=[
            fail(502, "Bad Gateway"),
            httpx.Response(503, text="<html>down</html>"),
            ok({"message_id": 9}),
        ]
    )
    assert adapter.publish(make_publication(video_file)).external_id == "9"
    assert sleeps == [1, 2]


def test_server_errors_give_up(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path, sleeps: list[float]
) -> None:
    api.post("/sendVideo").mock(return_value=fail(500, "Internal Server Error"))
    with pytest.raises(PublishError) as info:
        adapter.publish(make_publication(video_file))
    assert info.value.code == "telegram_unavailable"
    assert info.value.retryable is True
    assert sleeps == [1, 2, 4]


def test_connect_error_retries_then_succeeds(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path, sleeps: list[float]
) -> None:
    api.post("/sendVideo").mock(side_effect=[httpx.ConnectError("no route"), ok({"message_id": 3})])
    assert adapter.publish(make_publication(video_file)).external_id == "3"
    assert sleeps == [1]


def test_no_network_gives_up(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path, sleeps: list[float]
) -> None:
    api.post("/sendVideo").mock(side_effect=httpx.ConnectError("offline"))
    with pytest.raises(PublishError) as info:
        adapter.publish(make_publication(video_file))
    assert info.value.code == "network_error"
    assert info.value.retryable is True
    assert len(sleeps) == 3


def test_upload_read_timeout_is_not_retried(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path, sleeps: list[float]
) -> None:
    """The video may already be posted; retrying could publish it twice."""
    route = api.post("/sendVideo").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PublishError) as info:
        adapter.publish(make_publication(video_file))
    assert info.value.code == "telegram_timeout_uncertain"
    assert info.value.retryable is False
    assert route.call_count == 1
    assert sleeps == []


def test_connection_dropped_mid_upload_is_not_retried(
    adapter: TelegramBotAdapter, api: respx.MockRouter, video_file: Path
) -> None:
    route = api.post("/sendVideo").mock(side_effect=httpx.RemoteProtocolError("reset"))
    with pytest.raises(PublishError) as info:
        adapter.publish(make_publication(video_file))
    assert info.value.code == "network_error"
    assert route.call_count == 1


# --- authenticate ------------------------------------------------------------------


def test_authenticate_ok_for_admin_with_post_rights(
    adapter: TelegramBotAdapter, api: respx.MockRouter
) -> None:
    api.post("/getMe").mock(return_value=ok({"id": 777, "is_bot": True}))
    member = api.post("/getChatMember").mock(
        return_value=ok({"status": "administrator", "can_post_messages": True})
    )
    adapter.authenticate()
    body = member.calls.last.request.content.decode()
    assert "chat_id=%40my_channel" in body
    assert "user_id=777" in body


def test_authenticate_ok_for_creator(adapter: TelegramBotAdapter, api: respx.MockRouter) -> None:
    api.post("/getMe").mock(return_value=ok({"id": 1}))
    api.post("/getChatMember").mock(return_value=ok({"status": "creator"}))
    adapter.authenticate()


@pytest.mark.parametrize(
    "member",
    [
        {"status": "administrator", "can_post_messages": False},
        {"status": "administrator"},
        {"status": "member"},
        {"status": "left"},
    ],
)
def test_authenticate_rejects_bot_without_post_rights(
    adapter: TelegramBotAdapter, api: respx.MockRouter, member: dict[str, object]
) -> None:
    api.post("/getMe").mock(return_value=ok({"id": 1}))
    api.post("/getChatMember").mock(return_value=ok(member))
    with pytest.raises(PublishError) as info:
        adapter.authenticate()
    assert info.value.code == "telegram_bot_not_admin"


def test_authenticate_invalid_token(adapter: TelegramBotAdapter, api: respx.MockRouter) -> None:
    api.post("/getMe").mock(return_value=fail(401, "Unauthorized"))
    with pytest.raises(PublishError) as info:
        adapter.authenticate()
    assert info.value.code == "telegram_invalid_token"


def test_authenticate_channel_not_found(adapter: TelegramBotAdapter, api: respx.MockRouter) -> None:
    api.post("/getMe").mock(return_value=ok({"id": 1}))
    api.post("/getChatMember").mock(return_value=fail(400, "Bad Request: chat not found"))
    with pytest.raises(PublishError) as info:
        adapter.authenticate()
    assert info.value.code == "telegram_chat_not_found"


def test_read_timeout_without_upload_is_retried(
    adapter: TelegramBotAdapter, api: respx.MockRouter, sleeps: list[float]
) -> None:
    api.post("/getMe").mock(side_effect=[httpx.ReadTimeout("slow"), ok({"id": 1})])
    api.post("/getChatMember").mock(return_value=ok({"status": "creator"}))
    adapter.authenticate()
    assert sleeps == [1]


def test_read_timeout_without_upload_gives_up(
    adapter: TelegramBotAdapter, api: respx.MockRouter, sleeps: list[float]
) -> None:
    api.post("/getMe").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PublishError) as info:
        adapter.authenticate()
    assert info.value.code == "telegram_timeout_uncertain"
    assert len(sleeps) == 3


# --- secrecy -----------------------------------------------------------------------


def test_token_not_logged(
    adapter: TelegramBotAdapter, api: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    api.post("/getMe").mock(return_value=fail(401, "Unauthorized"))
    with caplog.at_level(logging.DEBUG, logger="video_publisher"), pytest.raises(PublishError):
        adapter.authenticate()
    assert TOKEN not in caplog.text
    assert logging.getLogger("httpx").level == logging.WARNING


def test_token_not_in_error(adapter: TelegramBotAdapter, api: respx.MockRouter) -> None:
    api.post("/getMe").mock(side_effect=httpx.ConnectError(f"failed {BASE}/getMe"))
    with pytest.raises(PublishError) as info:
        adapter.authenticate()
    assert TOKEN not in repr(info.value)
    assert TOKEN not in str(info.value)


def test_default_client_has_upload_timeouts() -> None:
    adapter = TelegramBotAdapter(TOKEN, CHANNEL)
    timeout = adapter._client.timeout
    assert timeout.connect == 10.0
    assert timeout.write == 600.0
