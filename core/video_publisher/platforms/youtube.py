"""YouTube publishing through the YouTube Data API v3 (``videos.insert``).

Upload, as chosen by the owner: the whole file in ONE request, no chunks and no resuming.
Technically it is the "resumable" protocol used in its simplest form: a small request
creates an upload session with the video's details (safe to repeat, nothing is posted
yet), then a single ``PUT`` sends the whole file. A timeout or a dropped connection
during that ``PUT`` is NOT retried: YouTube may already have the video, and a retry
would post it twice.

Shorts: the API has no "Short" flag. YouTube itself treats vertical or square videos up
to 3 minutes as Shorts, so the same upload serves both; only the link differs.

Scheduling is native: the video is uploaded as private with ``status.publishAt`` and
YouTube makes it public at that time, even when the computer is off. Deleting such a
record removes the video from YouTube (owner's choice), so it never goes out.

Access: a refresh token from the Google login (see ``auth/youtube_login.py``) is turned
into a short-lived access token before each operation.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx

from video_publisher.domain import Platform, PostFormat, PostText, Publication, Video
from video_publisher.platforms.base import PlatformAdapter, PublishResult, ValidationResult
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.facebook_page import build_description

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

MAX_UPLOAD_BYTES = 256 * 1024**3  # YouTube's limit: 256 GB
SUPPORTED_EXTENSIONS = frozenset({".mp4"})
MAX_TITLE = 100
MAX_DESCRIPTION_BYTES = 5000
UPLOAD_CHUNK = 1024 * 1024  # read size while streaming the file (still one request)

# Tokens travel in headers and form data; httpx logs URLs at INFO level.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=1800.0, pool=10.0)

# ``error.errors[].reason`` / ``error.details[].reason`` values.
_QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded", "RATE_LIMIT_EXCEEDED"})
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
_DISABLED_REASONS = frozenset({"accessNotConfigured", "SERVICE_DISABLED"})


def video_url(video_id: str, *, short: bool = False) -> str:
    if short:
        return f"https://www.youtube.com/shorts/{video_id}"
    return f"https://www.youtube.com/watch?v={video_id}"


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def error_reasons(body: Any) -> list[str]:
    """Reasons from a Google API error body (both the old and the new format)."""
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return []
    err = body["error"]
    reasons: list[str] = []
    for key in ("errors", "details"):
        items = err.get(key)
        if isinstance(items, list):
            reasons += [str(i["reason"]) for i in items if isinstance(i, dict) and i.get("reason")]
    return reasons


def classify(status: int, reasons: list[str]) -> tuple[str, bool]:
    """Map a YouTube API failure to ``(our error code, retryable)``."""
    found = set(reasons)
    if status == 401:
        return "youtube_token_invalid", False
    if "youtubeSignupRequired" in found:
        return "youtube_no_channel", False
    if "uploadLimitExceeded" in found:
        return "youtube_upload_limit", False
    if found & _QUOTA_REASONS:
        return "youtube_quota_exceeded", False
    if found & _RATE_REASONS or status == 429:
        return "youtube_rate_limited", True
    if found & _DISABLED_REASONS:
        return "youtube_api_disabled", False
    if status >= 500:
        return "youtube_unavailable", True
    if status == 403:
        return "youtube_permission_denied", False
    if status == 404:
        return "youtube_video_not_found", False
    return "youtube_rejected", False


class GoogleClient:
    """Small Google API client: OAuth tokens, JSON calls with retries, the file upload."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._max_retries = max_retries
        self._sleep = sleep

    # --- OAuth ------------------------------------------------------------------------

    def token(self, form: dict[str, str]) -> dict[str, Any]:
        """``POST`` to the token endpoint (code exchange or refresh)."""
        resp = self._send(lambda: self._client.post(TOKEN_URL, data=form), retry_timeouts=True)
        body = _json(resp)
        if resp.is_success and isinstance(body, dict) and body.get("access_token"):
            return body
        error = body.get("error") if isinstance(body, dict) else None
        if error == "invalid_grant":
            raise PublishError("youtube_token_invalid")
        if error in ("invalid_client", "unauthorized_client"):
            raise PublishError("youtube_client_invalid")
        if resp.status_code >= 500:
            raise PublishError("youtube_unavailable", retryable=True, status=resp.status_code)
        raise PublishError("youtube_rejected", status=resp.status_code)

    # --- API calls --------------------------------------------------------------------

    def request(self, method: str, url: str, token: str, **kwargs: Any) -> httpx.Response:
        """Call an API endpoint that uploads nothing; retried on transient errors.

        Returns the successful response (callers read the body or headers they need).
        """
        headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        attempt = 0
        while True:
            attempt += 1
            resp = self._send(
                lambda: self._client.request(method, url, headers=headers, **kwargs),
                retry_timeouts=method in ("GET", "DELETE") or "uploadType" in url,
            )
            if resp.is_success:
                return resp
            code, retryable = classify(resp.status_code, error_reasons(_json(resp)))
            if retryable and attempt <= self._max_retries:
                self._sleep(self._backoff(attempt))
                continue
            reasons = error_reasons(_json(resp))
            params: dict[str, Any] = {"status": resp.status_code}
            if reasons:
                params["reason"] = reasons[0]
            raise PublishError(code, retryable=retryable, **params)

    def put_file(self, url: str, file: Path, token: str) -> dict[str, Any]:
        """Send the whole file in one ``PUT``. Only a failure to connect is retried."""
        size = file.stat().st_size
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._client.put(
                    url,
                    content=_read_chunks(file),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "video/mp4",
                        "Content-Length": str(size),
                    },
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                if attempt > self._max_retries:
                    raise PublishError("network_error", retryable=True) from exc
                self._sleep(self._backoff(attempt))
                continue
            except httpx.TransportError as exc:
                raise PublishError("youtube_timeout_uncertain") from exc
            body = _json(resp)
            if resp.is_success and isinstance(body, dict) and body.get("id"):
                return body
            if resp.status_code >= 500:
                # A gateway error says nothing about whether the video was created.
                raise PublishError("youtube_timeout_uncertain", status=resp.status_code)
            code, _ = classify(resp.status_code, error_reasons(body))
            reasons = error_reasons(body)
            params: dict[str, Any] = {"status": resp.status_code}
            if reasons:
                params["reason"] = reasons[0]
            raise PublishError(code, **params)

    # --- helpers ----------------------------------------------------------------------

    def _send(self, call: Callable[[], httpx.Response], *, retry_timeouts: bool) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                return call()
            except httpx.TimeoutException as exc:
                connect = isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout))
                if (connect or retry_timeouts) and attempt <= self._max_retries:
                    self._sleep(self._backoff(attempt))
                    continue
                raise PublishError("network_error", retryable=True) from exc
            except httpx.ConnectError as exc:
                if attempt > self._max_retries:
                    raise PublishError("network_error", retryable=True) from exc
                self._sleep(self._backoff(attempt))
            except httpx.TransportError as exc:
                raise PublishError("network_error", retryable=True) from exc

    @staticmethod
    def _backoff(attempt: int) -> float:
        return float(min(2 ** (attempt - 1), 30))


def _read_chunks(file: Path) -> Iterator[bytes]:
    with file.open("rb") as fh:
        while chunk := fh.read(UPLOAD_CHUNK):
            yield chunk


def default_title(text: PostText, video: Video) -> str:
    """YouTube requires a title: fall back to the file name."""
    return text.title.strip() or video.path.stem.strip()[:MAX_TITLE]


def is_short(video: Video) -> bool:
    """Vertical or square and up to 3 minutes (needs metadata; unknown means no)."""
    if not video.width or not video.height or video.duration_seconds is None:
        return False
    return video.height >= video.width and video.duration_seconds <= 180


class YouTubeAdapter(PlatformAdapter):
    platform: ClassVar[Platform] = Platform.YOUTUBE
    supported_formats: ClassVar[tuple[PostFormat, ...]] = (PostFormat.VIDEO, PostFormat.SHORT)
    # A short lead keeps "publishAt" in the future after a long upload.
    min_schedule_lead: ClassVar[timedelta] = timedelta(minutes=15)
    max_schedule_ahead: ClassVar[timedelta] = timedelta(days=365)

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        privacy: str = "public",
        made_for_kids: bool = False,
        category_id: str = "22",
        tags: list[str] | None = None,
        google: GoogleClient | None = None,
        on_token_invalid: Callable[[], None] | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._privacy = privacy
        self._made_for_kids = made_for_kids
        self._category_id = category_id
        self._tags = list(tags or [])
        self._google = google or GoogleClient()
        self._on_token_invalid = on_token_invalid

    @property
    def supports_native_scheduling(self) -> bool:
        return True

    def _access_token(self) -> str:
        try:
            body = self._google.token(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                }
            )
        except PublishError as exc:
            if exc.code == "youtube_token_invalid" and self._on_token_invalid is not None:
                self._on_token_invalid()  # revoked or expired: Settings ask to log in again
            raise
        return str(body["access_token"])

    def authenticate(self) -> None:
        self._access_token()

    def validate(self, video: Video) -> ValidationResult:
        path = video.path
        if not path.is_file():
            return ValidationResult(ok=False, errors=["video_not_found"])
        errors: list[str] = []
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            errors.append("youtube_unsupported_format")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            errors.append("youtube_file_too_large")
        return ValidationResult(ok=not errors, errors=errors)

    def check_text(self, publication: Publication) -> None:
        """Title and description limits; raised before anything is uploaded."""
        title = default_title(publication.text, publication.video)
        if len(title) > MAX_TITLE:
            raise PublishError("youtube_title_too_long", max=MAX_TITLE, length=len(title))
        description = build_description(publication.text)
        size = len(description.encode("utf-8"))
        if size > MAX_DESCRIPTION_BYTES:
            raise PublishError(
                "youtube_description_too_long", max=MAX_DESCRIPTION_BYTES, length=size
            )
        if any(c in title + description for c in "<>"):
            raise PublishError("youtube_angle_brackets")

    def _check(self, publication: Publication) -> None:
        check = self.validate(publication.video)
        if not check.ok:
            raise PublishError(check.errors[0])
        self.check_text(publication)

    def _metadata(self, publication: Publication, at: datetime | None) -> dict[str, Any]:
        snippet: dict[str, Any] = {
            "title": default_title(publication.text, publication.video),
            "description": build_description(publication.text),
            "categoryId": self._category_id,
        }
        if self._tags:
            snippet["tags"] = self._tags
        status: dict[str, Any] = {
            "privacyStatus": self._privacy,
            "selfDeclaredMadeForKids": self._made_for_kids,
        }
        if at is not None:
            # YouTube requires "private" for a scheduled video; it turns public at ``at``.
            status["privacyStatus"] = "private"
            status["publishAt"] = at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"snippet": snippet, "status": status}

    def _upload(self, publication: Publication, at: datetime | None) -> PublishResult:
        self._check(publication)
        token = self._access_token()
        video = publication.video
        session = self._google.request(
            "POST",
            f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
            token,
            json=self._metadata(publication, at),
            headers={
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(video.path.stat().st_size),
            },
        )
        location = session.headers.get("Location")
        if not location:
            raise PublishError("youtube_rejected", status=session.status_code)
        body = self._google.put_file(location, video.path, token)
        video_id = str(body["id"])
        return PublishResult(external_id=video_id, url=video_url(video_id, short=is_short(video)))

    def publish(self, publication: Publication) -> PublishResult:
        return self._upload(publication, None)

    def schedule(self, publication: Publication, at: datetime) -> PublishResult:
        if at.tzinfo is None:
            raise PublishError("schedule_timezone_required")
        return self._upload(publication, at)

    def cancel_scheduled(self, external_id: str) -> None:
        """Delete the scheduled (still private) video from YouTube."""
        token = self._access_token()
        try:
            self._google.request("DELETE", f"{API_BASE}/videos", token, params={"id": external_id})
        except PublishError as exc:
            if exc.code == "youtube_video_not_found":
                return  # already deleted in YouTube Studio
            raise
