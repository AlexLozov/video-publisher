"""Publishing from the user's own Telegram account (Telethon fully faked) and scheduling."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from telethon import errors
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo

from fake_telegram_account import SESSION_OK, FakeTelegramAccount
from video_publisher.domain import Platform, PostFormat, PostText, Publication, PublishTarget, Video
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.scheduling import check_schedule_time
from video_publisher.platforms.telegram_account import (
    MAX_SCHEDULE_AHEAD,
    MAX_UPLOAD_BYTES,
    TelegramAccountAdapter,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def world() -> FakeTelegramAccount:
    return FakeTelegramAccount()


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 2048)
    return path


class Invalidated:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


def make_adapter(
    world: FakeTelegramAccount,
    channel: str = "@my_channel",
    session: str = SESSION_OK,
    on_invalid: Invalidated | None = None,
) -> TelegramAccountAdapter:
    return TelegramAccountAdapter(
        api_id=4242,
        api_hash="0123456789abcdef0123456789abcdef",
        session=session,
        channel=channel,
        client_factory=world.factory,
        on_session_invalid=on_invalid,
        now=lambda: NOW,
    )


def make_pub(path: Path, text: PostText | None = None, **video: Any) -> Publication:
    return Publication(
        video=Video(path=path, **video),
        target=PublishTarget(platform=Platform.TELEGRAM, format=PostFormat.VIDEO),
        text=text or PostText(title="Title", description="Body", hashtags=["travel", "#fun"]),
    )


def publish_code(adapter: TelegramAccountAdapter, pub: Publication) -> PublishError:
    with pytest.raises(PublishError) as info:
        adapter.publish(pub)
    return info.value


# --- scheduling window ------------------------------------------------------------------


def test_schedule_window_accepts_valid_time() -> None:
    check_schedule_time(NOW + timedelta(hours=1), NOW, MAX_SCHEDULE_AHEAD)
    check_schedule_time(NOW + MAX_SCHEDULE_AHEAD, NOW, MAX_SCHEDULE_AHEAD)


def test_schedule_window_compares_across_time_zones() -> None:
    moscow = timezone(timedelta(hours=3))
    # 15:30 in Moscow is 12:30 UTC — 30 minutes after NOW.
    check_schedule_time(datetime(2026, 11, 7, 15, 30, tzinfo=moscow), NOW, MAX_SCHEDULE_AHEAD)
    # 14:30 in Moscow is 11:30 UTC — already in the past.
    with pytest.raises(PublishError) as info:
        check_schedule_time(datetime(2026, 11, 7, 14, 30, tzinfo=moscow), NOW, MAX_SCHEDULE_AHEAD)
    assert info.value.code == "schedule_in_past"


@pytest.mark.parametrize(
    ("at", "code", "params"),
    [
        (NOW - timedelta(days=1), "schedule_in_past", {"min_minutes": 1}),
        (NOW, "schedule_in_past", {"min_minutes": 1}),
        (NOW + timedelta(seconds=30), "schedule_in_past", {"min_minutes": 1}),
        (NOW + timedelta(days=365, seconds=1), "schedule_too_far", {"max_days": 365}),
        (datetime(2026, 12, 1, 12, 0), "schedule_timezone_required", {}),
    ],
)
def test_schedule_window_rejects(at: datetime, code: str, params: dict[str, Any]) -> None:
    with pytest.raises(PublishError) as info:
        check_schedule_time(at, NOW, MAX_SCHEDULE_AHEAD)
    assert (info.value.code, info.value.params) == (code, params)


# --- validate ---------------------------------------------------------------------------


def test_validate_ok(world: FakeTelegramAccount, video_file: Path) -> None:
    assert make_adapter(world).validate(Video(path=video_file)).ok


def test_validate_missing_file(world: FakeTelegramAccount, tmp_path: Path) -> None:
    result = make_adapter(world).validate(Video(path=tmp_path / "nope.mp4"))
    assert result.errors == ["video_not_found"]


@pytest.mark.parametrize("name", ["clip.mov", "clip.mkv", "clip"])
def test_validate_only_mp4(world: FakeTelegramAccount, tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"x")
    assert make_adapter(world).validate(Video(path=path)).errors == ["telegram_unsupported_format"]


def test_validate_accepts_bot_size_limit_but_not_2gb(
    world: FakeTelegramAccount, tmp_path: Path
) -> None:
    big = tmp_path / "big.mp4"
    with big.open("wb") as fh:  # sparse: no real disk usage
        fh.truncate(MAX_UPLOAD_BYTES)
    assert make_adapter(world).validate(Video(path=big)).ok
    with big.open("r+b") as fh:
        fh.truncate(MAX_UPLOAD_BYTES + 1)
    assert make_adapter(world).validate(Video(path=big)).errors == [
        "telegram_account_file_too_large"
    ]


def test_publish_too_large_reports_sizes(world: FakeTelegramAccount, tmp_path: Path) -> None:
    big = tmp_path / "big.mp4"
    with big.open("wb") as fh:
        fh.truncate(MAX_UPLOAD_BYTES + 200 * 1024 * 1024)
    err = publish_code(make_adapter(world), make_pub(big))
    assert err.code == "telegram_account_file_too_large"
    assert err.params == {"max_mb": 2048, "size_mb": 2248.0}
    assert world.clients == []


# --- publish ----------------------------------------------------------------------------


def test_publish_now(world: FakeTelegramAccount, video_file: Path) -> None:
    result = make_adapter(world).publish(make_pub(video_file))
    assert result.external_id == "100"
    assert result.url == "https://t.me/my_channel/100"

    (upload_args, upload_kwargs) = world.called("upload_file")[0]
    assert upload_args == (str(video_file),)
    assert upload_kwargs == {"file_size": 2048, "file_name": "clip.mp4"}

    sent = world.sent[0]
    assert sent["entity"] == "peer:@my_channel"
    assert sent["caption"] == "Title\n\nBody\n\n#travel #fun"
    assert sent["parse_mode"] is None  # plain text, no Markdown mangling
    assert sent["supports_streaming"] is True
    assert sent["mime_type"] == "video/mp4"
    assert sent["schedule"] is None
    assert [type(a) for a in sent["attributes"]] == [DocumentAttributeFilename]
    assert world.clients[0].disconnected


def test_publish_without_text_sends_no_caption(
    world: FakeTelegramAccount, video_file: Path
) -> None:
    make_adapter(world).publish(make_pub(video_file, PostText()))
    assert world.sent[0]["caption"] is None


def test_publish_passes_known_video_dimensions(
    world: FakeTelegramAccount, video_file: Path
) -> None:
    make_adapter(world).publish(
        make_pub(video_file, duration_seconds=12.5, width=1080, height=1920)
    )
    video_attr = world.sent[0]["attributes"][1]
    assert isinstance(video_attr, DocumentAttributeVideo)
    assert (video_attr.w, video_attr.h, video_attr.duration) == (1080, 1920, 12.5)
    assert video_attr.supports_streaming


def test_publish_to_numeric_channel_loads_dialogs_first(
    world: FakeTelegramAccount, video_file: Path
) -> None:
    result = make_adapter(world, channel="-1001234567890").publish(make_pub(video_file))
    assert world.sent[0]["entity"] == "peer:-1001234567890"
    assert len(world.called("get_dialogs")) == 1
    assert world.called("get_input_entity")[0][0] == (-1001234567890,)
    assert result.url == "https://t.me/c/1234567890/100"


def test_unknown_numeric_channel(world: FakeTelegramAccount, video_file: Path) -> None:
    err = publish_code(make_adapter(world, channel="-1009999999999"), make_pub(video_file))
    assert err.code == "telegram_chat_not_found"
    assert world.called("upload_file") == []


def test_unknown_username(world: FakeTelegramAccount, video_file: Path) -> None:
    err = publish_code(make_adapter(world, channel="@missing_channel"), make_pub(video_file))
    assert err.code == "telegram_chat_not_found"
    assert world.called("get_dialogs") == []


def test_caption_too_long_checked_before_upload(
    world: FakeTelegramAccount, video_file: Path
) -> None:
    err = publish_code(make_adapter(world), make_pub(video_file, PostText(title="x" * 1025)))
    assert err.code == "caption_too_long"
    assert err.params == {"max": 1024, "length": 1025}
    assert world.clients == []


def test_missing_file_at_publish_time(world: FakeTelegramAccount, tmp_path: Path) -> None:
    err = publish_code(make_adapter(world), make_pub(tmp_path / "gone.mp4"))
    assert err.code == "video_not_found"


# --- schedule ---------------------------------------------------------------------------


def test_schedule_uses_telegram_schedule_date(world: FakeTelegramAccount, video_file: Path) -> None:
    at = NOW + timedelta(days=3)
    result = make_adapter(world).schedule(make_pub(video_file), at)
    assert world.sent[0]["schedule"] == at
    assert result.external_id == "100"
    assert result.url is None  # no public link until Telegram posts it


@pytest.mark.parametrize(
    ("at", "code"),
    [
        (NOW - timedelta(minutes=5), "schedule_in_past"),
        (NOW + timedelta(days=366), "schedule_too_far"),
    ],
)
def test_schedule_validates_time_before_connecting(
    world: FakeTelegramAccount, video_file: Path, at: datetime, code: str
) -> None:
    with pytest.raises(PublishError) as info:
        make_adapter(world).schedule(make_pub(video_file), at)
    assert info.value.code == code
    assert world.clients == []


@pytest.mark.parametrize(
    ("exc", "code", "params"),
    [
        (errors.ScheduleDateTooLateError(request=None), "schedule_too_far", {"max_days": 365}),
        (errors.ScheduleDateInvalidError(request=None), "schedule_in_past", {"min_minutes": 1}),
        (errors.ScheduleTooMuchError(request=None), "telegram_schedule_limit", {}),
    ],
)
def test_schedule_errors_from_telegram(
    world: FakeTelegramAccount,
    video_file: Path,
    exc: Exception,
    code: str,
    params: dict[str, Any],
) -> None:
    world.fail("send_file", exc)
    with pytest.raises(PublishError) as info:
        make_adapter(world).schedule(make_pub(video_file), NOW + timedelta(days=1))
    assert (info.value.code, info.value.params) == (code, params)


# --- errors -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "exc", "code"),
    [
        ("get_input_entity", errors.ChannelPrivateError(request=None), "telegram_chat_not_found"),
        (
            "get_input_entity",
            errors.UsernameNotOccupiedError(request=None),
            "telegram_chat_not_found",
        ),
        ("get_input_entity", errors.UsernameInvalidError(request=None), "telegram_chat_not_found"),
        ("send_file", errors.ChannelInvalidError(request=None), "telegram_chat_not_found"),
        ("send_file", errors.PeerIdInvalidError(request=None), "telegram_chat_not_found"),
        ("send_file", errors.ChatAdminRequiredError(request=None), "telegram_account_not_admin"),
        ("send_file", errors.ChatWriteForbiddenError(request=None), "telegram_account_not_admin"),
        (
            "send_file",
            errors.ChatSendMediaForbiddenError(request=None),
            "telegram_account_not_admin",
        ),
        ("send_file", errors.UserBannedInChannelError(request=None), "telegram_account_not_admin"),
        ("send_file", errors.MediaCaptionTooLongError(request=None), "caption_too_long"),
        ("send_file", errors.RPCError(request=None, message="WEIRD"), "telegram_rejected"),
        ("upload_file", errors.FilePartsInvalidError(request=None), "telegram_rejected"),
        ("connect", errors.ApiIdInvalidError(request=None), "telegram_api_keys_invalid"),
    ],
)
def test_platform_errors(
    world: FakeTelegramAccount, video_file: Path, method: str, exc: Exception, code: str
) -> None:
    world.fail(method, exc)
    err = publish_code(make_adapter(world), make_pub(video_file))
    assert err.code == code
    assert not err.retryable
    assert world.clients[0].disconnected


def test_flood_wait(world: FakeTelegramAccount, video_file: Path) -> None:
    world.fail("send_file", errors.FloodWaitError(request=None, capture=300))
    err = publish_code(make_adapter(world), make_pub(video_file))
    assert (err.code, err.params, err.retryable) == ("telegram_flood_wait", {"seconds": 300}, True)


@pytest.mark.parametrize("method", ["connect", "get_input_entity", "upload_file"])
@pytest.mark.parametrize("exc", [ConnectionError("reset"), OSError("no route"), TimeoutError()])
def test_network_failure_before_posting_is_retryable(
    world: FakeTelegramAccount, video_file: Path, method: str, exc: Exception
) -> None:
    world.fail(method, exc)
    err = publish_code(make_adapter(world), make_pub(video_file))
    assert (err.code, err.retryable) == ("network_error", True)
    assert world.sent == []


@pytest.mark.parametrize("exc", [ConnectionError("reset"), TimeoutError()])
def test_network_failure_while_posting_is_uncertain(
    world: FakeTelegramAccount, video_file: Path, exc: Exception
) -> None:
    world.fail("send_file", exc)
    err = publish_code(make_adapter(world), make_pub(video_file))
    assert (err.code, err.retryable) == ("telegram_timeout_uncertain", False)
    assert len(world.called("send_file")) == 1  # never retried by us


@pytest.mark.parametrize(
    "exc",
    [
        errors.AuthKeyUnregisteredError(request=None),
        errors.SessionRevokedError(request=None),
        errors.SessionExpiredError(request=None),
        errors.UserDeactivatedError(request=None),
        errors.UserDeactivatedBanError(request=None),
        errors.AuthKeyDuplicatedError(request=None),
    ],
)
def test_revoked_session_is_reported_and_forgotten(
    world: FakeTelegramAccount, video_file: Path, exc: Exception
) -> None:
    invalidated = Invalidated()
    world.fail("send_file", exc)
    err = publish_code(make_adapter(world, on_invalid=invalidated), make_pub(video_file))
    assert err.code == "telegram_session_expired"
    assert invalidated.count == 1


def test_not_authorized_session(world: FakeTelegramAccount, video_file: Path) -> None:
    invalidated = Invalidated()
    adapter = make_adapter(world, session="dead-session", on_invalid=invalidated)
    err = publish_code(adapter, make_pub(video_file))
    assert err.code == "telegram_session_expired"
    assert invalidated.count == 1
    assert world.called("upload_file") == []


def test_other_errors_keep_the_session(world: FakeTelegramAccount, video_file: Path) -> None:
    invalidated = Invalidated()
    world.fail("send_file", errors.ChatAdminRequiredError(request=None))
    publish_code(make_adapter(world, on_invalid=invalidated), make_pub(video_file))
    assert invalidated.count == 0


def test_session_expired_without_callback(world: FakeTelegramAccount, video_file: Path) -> None:
    err = publish_code(make_adapter(world, session="dead"), make_pub(video_file))
    assert err.code == "telegram_session_expired"


# --- authenticate -----------------------------------------------------------------------


def test_authenticate_ok(world: FakeTelegramAccount) -> None:
    make_adapter(world).authenticate()
    assert world.clients[0].disconnected


def test_authenticate_dead_session(world: FakeTelegramAccount) -> None:
    invalidated = Invalidated()
    with pytest.raises(PublishError) as info:
        make_adapter(world, session="dead", on_invalid=invalidated).authenticate()
    assert info.value.code == "telegram_session_expired"
    assert invalidated.count == 1


def test_authenticate_offline(world: FakeTelegramAccount) -> None:
    world.fail("connect", ConnectionError())
    with pytest.raises(PublishError) as info:
        make_adapter(world).authenticate()
    assert info.value.code == "network_error"


def test_supports_native_scheduling(world: FakeTelegramAccount) -> None:
    adapter = make_adapter(world)
    assert adapter.supports_native_scheduling
    assert adapter.max_schedule_ahead == timedelta(days=365)


# --- real Telethon client construction (no network) -------------------------------------


def test_default_factory_builds_a_string_session_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    from video_publisher.platforms.telegram_mtproto import default_client_factory, run

    monkeypatch.chdir(tmp_path)

    async def build() -> str:
        client = default_client_factory("", 4242, "0123456789abcdef0123456789abcdef")
        assert isinstance(client, TelegramClient)
        assert isinstance(client.session, StringSession)
        return client.session.save()

    assert run(build()) == ""
    assert list(tmp_path.iterdir()) == []  # no .session file on disk
