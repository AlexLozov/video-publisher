"""Publish now: validate synchronously, then upload in the background.

Flow:
1. ``start()`` builds the adapter from settings and validates the video. Problems the user
   can fix (not configured, wrong format, too large...) are raised right away as
   ``PublishError`` and no record is created.
2. A ``publications`` row is created with status ``publishing`` and the upload runs on a
   worker thread. The UI polls the row; the screen may be closed meanwhile.
3. The row ends as ``published`` (with link) or ``failed`` (with error code and params).

If the app is closed during an upload, the row stays ``publishing``; on the next start
``recover_interrupted()`` marks it ``failed`` with ``interrupted`` so the user can check
the channel (the post may or may not have gone out).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from video_publisher.auth.secret_store import SecretName
from video_publisher.domain import (
    Platform,
    PostFormat,
    PostText,
    Publication,
    PublicationStatus,
    PublishTarget,
    Video,
)
from video_publisher.platforms.base import PlatformAdapter
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.telegram_bot import TelegramBotAdapter
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


class PublicationView(BaseModel):
    id: str
    platform: Platform
    method: str
    target: str | None
    video_path: str
    title: str
    status: PublicationStatus
    url: str | None
    error_code: str | None
    error_params: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row: PublicationRow) -> PublicationView:
        return cls(
            id=row.id,
            platform=Platform(row.platform),
            method=row.method,
            target=row.target,
            video_path=row.video_path,
            title=row.title,
            status=PublicationStatus(row.status),
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


def default_adapter_factory(platform: Platform, settings: SettingsService) -> BuiltAdapter:
    """Build the adapter for a platform from the user's settings."""
    if platform is not Platform.TELEGRAM:
        raise PublishError("platform_not_supported", platform=platform.value)

    tg = settings.get().telegram
    if tg.mode is TelegramMode.ACCOUNT:
        raise PublishError("telegram_account_not_available")

    token = settings.get_secret(SecretName.TELEGRAM_BOT_TOKEN)
    missing = [name for name, value in (("channel", tg.channel), ("bot_token", token)) if not value]
    if missing:
        raise PublishError("telegram_not_configured", missing=missing)
    assert token is not None and tg.channel is not None
    return BuiltAdapter(TelegramBotAdapter(token, tg.channel), TelegramMode.BOT.value, tg.channel)


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
    ) -> None:
        self._sessions = session_factory
        self._settings = settings
        self._factory = adapter_factory
        self._executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="pub")

    def start(self, request: PublishRequest) -> PublicationView:
        built = self._factory(request.platform, self._settings)
        text = PostText(
            title=request.title.strip(),
            description=request.description.strip(),
            hashtags=_clean_hashtags(request.hashtags),
        )
        publication = Publication(
            video=Video(path=Path(request.video_path.strip())),
            target=PublishTarget(
                platform=request.platform, format=PostFormat.VIDEO, account_id=built.target
            ),
            text=text,
            status=PublicationStatus.PUBLISHING,
        )

        check = built.adapter.validate(publication.video)
        if not check.ok:
            raise PublishError(check.errors[0], errors=check.errors)

        row = PublicationRow(
            id=str(publication.id),
            platform=request.platform.value,
            method=built.method,
            target=built.target,
            video_path=str(publication.video.path),
            title=text.title,
            description=text.description,
            hashtags=text.hashtags,
            status=PublicationStatus.PUBLISHING.value,
            error_params={},
        )
        with self._sessions.begin() as session:
            session.add(row)
            session.flush()
            view = PublicationView.from_row(row)

        self._executor.submit(self._run, built.adapter, publication)
        return view

    def _run(self, adapter: PlatformAdapter, publication: Publication) -> None:
        pub_id = str(publication.id)
        try:
            result = adapter.publish(publication)
        except PublishError as exc:
            self._finish(pub_id, PublicationStatus.FAILED, error=(exc.code, exc.params))
        except Exception:
            log.exception("Unexpected error while publishing %s", pub_id)
            self._finish(pub_id, PublicationStatus.FAILED, error=("internal_error", {}))
        else:
            self._finish(
                pub_id, PublicationStatus.PUBLISHED, external_id=result.external_id, url=result.url
            )

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
