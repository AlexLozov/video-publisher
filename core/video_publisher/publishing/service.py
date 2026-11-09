"""Publish now or schedule: validate synchronously, then upload in the background.

Flow:
1. ``start()`` builds the adapter from settings and validates the video. Problems the user
   can fix (not configured, wrong format, too large...) are raised right away as
   ``PublishError`` and no record is created.
2. A ``publications`` row is created with status ``publishing`` and the upload runs on a
   worker thread. The UI polls the row; the screen may be closed meanwhile.
3. The row ends as ``published`` (with link), ``scheduled`` (the platform holds the post
   until ``scheduled_at``) or ``failed`` (with error code and params).

Scheduling is only accepted for adapters that schedule natively (Telegram "my account",
Facebook Page, YouTube).
Publishing at a later time from the app itself comes with the scheduler.

If the app is closed during an upload, the row stays ``publishing``; on the next start
``recover_interrupted()`` marks it ``failed`` with ``interrupted`` so the user can check
the channel (the post may or may not have gone out).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from video_publisher.auth.secret_store import SecretName
from video_publisher.config import get_settings
from video_publisher.domain import (
    Platform,
    PostFormat,
    PostText,
    Publication,
    PublicationStatus,
    PublishTarget,
    Video,
)
from video_publisher.media.probe import probe
from video_publisher.platforms.base import PlatformAdapter
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.facebook_page import FacebookPageAdapter
from video_publisher.platforms.scheduling import check_schedule_time
from video_publisher.platforms.telegram_account import TelegramAccountAdapter
from video_publisher.platforms.telegram_bot import TelegramBotAdapter
from video_publisher.platforms.telegram_mtproto import ClientFactory, default_client_factory
from video_publisher.platforms.youtube import YouTubeAdapter
from video_publisher.storage.models import PublicationRow
from video_publisher.user_settings import SettingsService, TelegramMode

log = logging.getLogger(__name__)

MAX_HASHTAGS = 30


class PublishRequest(BaseModel):
    video_path: str = Field(min_length=1, max_length=4096)
    platform: Platform
    title: str = Field(default="", max_length=1000)
    description: str = Field(default="", max_length=5000)
    hashtags: list[str] = Field(default_factory=list, max_length=MAX_HASHTAGS)
    # Omitted or null: publish now. Must carry a time zone offset.
    scheduled_at: AwareDatetime | None = None


class BatchPublishRequest(BaseModel):
    """One video to several networks at once (the upload screen's checkboxes)."""

    video_path: str = Field(min_length=1, max_length=4096)
    platforms: list[Platform] = Field(min_length=1, max_length=len(Platform))
    title: str = Field(default="", max_length=1000)
    description: str = Field(default="", max_length=5000)
    hashtags: list[str] = Field(default_factory=list, max_length=MAX_HASHTAGS)
    scheduled_at: AwareDatetime | None = None

    def for_platform(self, platform: Platform) -> PublishRequest:
        return PublishRequest(
            video_path=self.video_path,
            platform=platform,
            title=self.title,
            description=self.description,
            hashtags=self.hashtags,
            scheduled_at=self.scheduled_at,
        )


class PublicationView(BaseModel):
    id: str
    # Publications started together (one upload to several networks) share it.
    batch_id: str
    platform: Platform
    method: str
    target: str | None
    video_path: str
    title: str
    status: PublicationStatus
    scheduled_at: datetime | None
    url: str | None
    error_code: str | None
    error_params: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: PublicationRow) -> PublicationView:
        return cls(
            id=row.id,
            batch_id=row.batch_id or row.id,
            platform=Platform(row.platform),
            method=row.method,
            target=row.target,
            video_path=row.video_path,
            title=row.title,
            status=PublicationStatus(row.status),
            scheduled_at=_as_utc(row.scheduled_at),
            url=row.url,
            error_code=row.error_code,
            error_params=row.error_params,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class BuiltAdapter:
    """An adapter ready to publish, plus how and where it publishes (for the record)."""

    def __init__(self, adapter: PlatformAdapter, method: str, target: str | None) -> None:
        self.adapter = adapter
        self.method = method
        self.target = target


AdapterFactory = Callable[[Platform, SettingsService], BuiltAdapter]


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops the offset of stored datetimes; they are always written in UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def make_adapter_factory(
    telegram_client_factory: ClientFactory = default_client_factory,
) -> AdapterFactory:
    """Build adapters from the user's settings (the Telegram client is replaceable in tests)."""

    def factory(platform: Platform, settings: SettingsService) -> BuiltAdapter:
        if platform is Platform.FACEBOOK:
            return _facebook_page(settings)
        if platform is Platform.YOUTUBE:
            return _youtube(settings)
        if platform is not Platform.TELEGRAM:
            raise PublishError("platform_not_supported", platform=platform.value)
        if settings.get().telegram.mode is TelegramMode.ACCOUNT:
            return _telegram_account(settings, telegram_client_factory)
        return _telegram_bot(settings)

    return factory


def _telegram_account(settings: SettingsService, client_factory: ClientFactory) -> BuiltAdapter:
    tg = settings.get().telegram
    api_hash = settings.get_secret(SecretName.TELEGRAM_API_HASH)
    session = settings.get_secret(SecretName.TELEGRAM_SESSION)
    missing = [
        name
        for name, value in (
            ("channel", tg.channel),
            ("api_id", tg.api_id),
            ("api_hash", api_hash),
            ("login", session),
        )
        if not value
    ]
    if missing:
        raise PublishError("telegram_account_not_configured", missing=missing)
    assert tg.channel is not None and tg.api_id is not None
    assert api_hash is not None and session is not None
    adapter = TelegramAccountAdapter(
        api_id=tg.api_id,
        api_hash=api_hash,
        session=session,
        channel=tg.channel,
        client_factory=client_factory,
        # A login revoked elsewhere cannot be repaired: forget it, Settings asks to log in.
        on_session_invalid=lambda: settings.delete_secret(SecretName.TELEGRAM_SESSION),
    )
    return BuiltAdapter(adapter, TelegramMode.ACCOUNT.value, tg.channel)


def _facebook_page(settings: SettingsService) -> BuiltAdapter:
    fb = settings.get().facebook
    token = settings.get_secret(SecretName.FACEBOOK_PAGE_TOKEN)
    if not fb.page_id or not token:
        raise PublishError("facebook_not_configured", missing=["login"])
    adapter = FacebookPageAdapter(
        fb.page_id,
        token,
        # A revoked or expired page token cannot be repaired: Settings ask to log in again.
        on_token_invalid=lambda: settings.delete_secret(SecretName.FACEBOOK_PAGE_TOKEN),
    )
    return BuiltAdapter(adapter, "page", fb.page_name or fb.page_id)


def _youtube(settings: SettingsService) -> BuiltAdapter:
    yt = settings.get().youtube
    secret = settings.get_secret(SecretName.YOUTUBE_CLIENT_SECRET)
    refresh = settings.get_secret(SecretName.YOUTUBE_REFRESH_TOKEN)
    missing = [
        name
        for name, value in (
            ("client_id", yt.client_id),
            ("client_secret", secret),
            ("login", refresh and yt.channel_id),
        )
        if not value
    ]
    if missing:
        raise PublishError("youtube_not_configured", missing=missing)
    assert yt.client_id is not None and secret is not None and refresh is not None
    adapter = YouTubeAdapter(
        client_id=yt.client_id,
        client_secret=secret,
        refresh_token=refresh,
        privacy=yt.privacy.value,
        made_for_kids=yt.made_for_kids,
        category_id=yt.category_id,
        tags=yt.tags,
        # A revoked or expired login cannot be repaired: Settings ask to log in again.
        on_token_invalid=lambda: settings.delete_secret(SecretName.YOUTUBE_REFRESH_TOKEN),
    )
    return BuiltAdapter(adapter, "channel", yt.channel_title or yt.channel_id)


def _telegram_bot(settings: SettingsService) -> BuiltAdapter:
    tg = settings.get().telegram
    token = settings.get_secret(SecretName.TELEGRAM_BOT_TOKEN)
    missing = [name for name, value in (("channel", tg.channel), ("bot_token", token)) if not value]
    if missing:
        raise PublishError("telegram_not_configured", missing=missing)
    assert token is not None and tg.channel is not None
    adapter = TelegramBotAdapter(token, tg.channel, base_url=get_settings().telegram_api_base)
    return BuiltAdapter(adapter, TelegramMode.BOT.value, tg.channel)


default_adapter_factory = make_adapter_factory()


def _clean_hashtags(tags: list[str]) -> list[str]:
    seen: list[str] = []
    for tag in tags:
        t = tag.strip().lstrip("#").strip()
        if t and t not in seen:
            seen.append(t)
    return seen


class PublishingService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: SettingsService,
        *,
        adapter_factory: AdapterFactory = default_adapter_factory,
        executor: Executor | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._now = now
        self._sessions = session_factory
        self._settings = settings
        self._factory = adapter_factory
        self._executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="pub")

    def start(self, request: PublishRequest) -> PublicationView:
        prepared = self._prepare(request)
        return self._launch([prepared])[0]

    def start_batch(self, request: BatchPublishRequest) -> list[PublicationView]:
        """Check every network first; publish only if all of them accept the video.

        A problem with any network is raised (with ``platform`` added to its params)
        before anything is uploaded, so the user fixes it and retries the whole upload.
        """
        platforms = list(dict.fromkeys(request.platforms))  # keep order, drop repeats
        prepared = []
        for platform in platforms:
            try:
                prepared.append(self._prepare(request.for_platform(platform)))
            except PublishError as exc:
                raise PublishError(exc.code, **{**exc.params, "platform": platform.value}) from exc
        return self._launch(prepared)

    def _launch(
        self, prepared: list[tuple[BuiltAdapter, Publication, PublishRequest]]
    ) -> list[PublicationView]:
        batch_id = str(prepared[0][1].id)
        views: list[PublicationView] = []
        with self._sessions.begin() as session:
            for built, publication, request in prepared:
                row = self._new_row(built, publication, request)
                row.batch_id = batch_id
                session.add(row)
                session.flush()
                views.append(PublicationView.from_row(row))
        for built, publication, _ in prepared:
            self._executor.submit(self._run, built.adapter, publication)
        return views

    def _prepare(self, request: PublishRequest) -> tuple[BuiltAdapter, Publication, PublishRequest]:
        built = self._factory(request.platform, self._settings)
        text = PostText(
            title=request.title.strip(),
            description=request.description.strip(),
            hashtags=_clean_hashtags(request.hashtags),
        )
        path = Path(request.video_path.strip())
        info = probe(path) if path.is_file() else None
        publication = Publication(
            video=Video(
                path=path,
                duration_seconds=info.duration_seconds if info else None,
                width=info.width if info else None,
                height=info.height if info else None,
            ),
            target=PublishTarget(
                platform=request.platform, format=PostFormat.VIDEO, account_id=built.target
            ),
            text=text,
            scheduled_at=request.scheduled_at,
            status=PublicationStatus.PUBLISHING,
        )

        check = built.adapter.validate(publication.video)
        if not check.ok:
            raise PublishError(check.errors[0], errors=check.errors)
        built.adapter.check_text(publication)
        if request.scheduled_at is not None:
            if not built.adapter.supports_native_scheduling:
                raise PublishError("scheduling_not_supported", method=built.method)
            adapter = built.adapter
            check_schedule_time(
                request.scheduled_at,
                self._now(),
                adapter.max_schedule_ahead,
                adapter.min_schedule_lead,
            )
        return built, publication, request

    @staticmethod
    def _new_row(
        built: BuiltAdapter, publication: Publication, request: PublishRequest
    ) -> PublicationRow:
        text = publication.text
        return PublicationRow(
            id=str(publication.id),
            platform=request.platform.value,
            method=built.method,
            target=built.target,
            video_path=str(publication.video.path),
            title=text.title,
            description=text.description,
            hashtags=text.hashtags,
            status=PublicationStatus.PUBLISHING.value,
            scheduled_at=(
                request.scheduled_at.astimezone(UTC) if request.scheduled_at is not None else None
            ),
            error_params={},
        )

    def _run(self, adapter: PlatformAdapter, publication: Publication) -> None:
        pub_id = str(publication.id)
        at = publication.scheduled_at
        try:
            result = adapter.schedule(publication, at) if at else adapter.publish(publication)
        except PublishError as exc:
            self._finish(pub_id, PublicationStatus.FAILED, error=(exc.code, exc.params))
        except Exception:
            log.exception("Unexpected error while publishing %s", pub_id)
            self._finish(pub_id, PublicationStatus.FAILED, error=("internal_error", {}))
        else:
            status = PublicationStatus.SCHEDULED if at else PublicationStatus.PUBLISHED
            self._finish(pub_id, status, external_id=result.external_id, url=result.url)

    def _finish(
        self,
        pub_id: str,
        status: PublicationStatus,
        *,
        external_id: str | None = None,
        url: str | None = None,
        error: tuple[str, dict[str, Any]] | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            row = session.get(PublicationRow, pub_id)
            if row is None:  # deleted meanwhile
                return
            row.status = status.value
            row.external_id = external_id
            row.url = url
            row.error_code = error[0] if error else None
            row.error_params = error[1] if error else {}

    def get(self, pub_id: str) -> PublicationView | None:
        with self._sessions() as session:
            row = session.get(PublicationRow, pub_id)
            return PublicationView.from_row(row) if row else None

    def recent(self, limit: int = 20) -> list[PublicationView]:
        with self._sessions() as session:
            rows = session.scalars(
                select(PublicationRow).order_by(PublicationRow.created_at.desc()).limit(limit)
            ).all()
            return [PublicationView.from_row(r) for r in rows]

    def delete(self, pub_id: str) -> bool:
        """Remove one record from the history. False if there is no such record.

        A post that is still waiting on the platform (``scheduled``) is withdrawn there
        first, so deleting it means it will not go out. If that fails, nothing is deleted
        and the error is raised. An upload in progress cannot be deleted.
        """
        with self._sessions() as session:
            row = session.get(PublicationRow, pub_id)
            if row is None:
                return False
            status, method, external_id = row.status, row.method, row.external_id
            platform = Platform(row.platform)

        if status == PublicationStatus.PUBLISHING.value:
            raise PublishError("publication_in_progress")
        if status == PublicationStatus.SCHEDULED.value and external_id:
            built = self._factory(platform, self._settings)
            if built.method != method:
                # Scheduled from "my account", but Settings now say "bot": cannot reach it.
                raise PublishError("cancel_needs_method", method=method)
            built.adapter.cancel_scheduled(external_id)

        with self._sessions.begin() as session:
            row = session.get(PublicationRow, pub_id)
            if row is not None:
                session.delete(row)
        return True

    def clear(self) -> int:
        """Remove finished records (published / failed). Scheduled and running ones stay,
        so clearing the history never cancels a post. Returns how many were removed."""
        finished = (PublicationStatus.PUBLISHED.value, PublicationStatus.FAILED.value)
        with self._sessions.begin() as session:
            rows = session.scalars(
                select(PublicationRow).where(PublicationRow.status.in_(finished))
            ).all()
            for row in rows:
                session.delete(row)
            return len(rows)

    def recover_interrupted(self) -> int:
        """Mark uploads left ``publishing`` by a previous run as failed. Returns the count."""
        with self._sessions.begin() as session:
            rows = session.scalars(
                select(PublicationRow).where(
                    PublicationRow.status == PublicationStatus.PUBLISHING.value
                )
            ).all()
            for row in rows:
                row.status = PublicationStatus.FAILED.value
                row.error_code = "interrupted"
                row.error_params = {}
            return len(rows)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
