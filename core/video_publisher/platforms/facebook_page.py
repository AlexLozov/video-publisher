"""Facebook Page publishing through the Meta Graph API (``POST /{page-id}/videos``).

The whole file goes in one multipart request (non-resumable upload, up to 1 GB), as chosen
by the owner. A timeout or a dropped connection while the file is being sent is NOT
retried: Facebook may already have created the post, and a retry would publish it twice.

Scheduling is native: the video is uploaded unpublished with ``scheduled_publish_time``
and Facebook publishes it itself, even when the computer is off.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx

from video_publisher.domain import Platform, PostFormat, PostText, Publication, Video
from video_publisher.platforms.base import PlatformAdapter, PublishResult, ValidationResult
from video_publisher.platforms.errors import PublishError

GRAPH_VERSION = "v24.0"
GRAPH_BASE = "https://graph.facebook.com"
# Video uploads go to a dedicated host.
VIDEO_BASE = "https://graph-video.facebook.com"
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
SUPPORTED_EXTENSIONS = frozenset({".mp4"})
MAX_TITLE = 255

# Access tokens travel in query strings and form data; httpx logs URLs at INFO level.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=1800.0, pool=10.0)

# Graph API error codes (https://developers.facebook.com/docs/graph-api/guides/error-handling).
_RATE_LIMIT_CODES = frozenset({4, 17, 32, 613, 80001})
_TRANSIENT_CODES = frozenset({1, 2})
_TOKEN_CODES = frozenset({102, 190})
_PERMISSION_CODES = frozenset({3, 10, *range(200, 300)})
_VIDEO_FORMAT_CODES = frozenset({352, 6000})


def build_description(text: PostText) -> str:
    """Description and hashtags separated by a blank line (the title is a separate field)."""
    tags = " ".join(
        tag if tag.startswith("#") else f"#{tag}"
        for tag in (t.strip() for t in text.hashtags)
        if tag
    )
    return "\n\n".join(p for p in (text.description.strip(), tags) if p)


def video_url(page_id: str, video_id: str) -> str:
    return f"https://www.facebook.com/{page_id}/videos/{video_id}"


def graph_error(body: Any) -> tuple[int | None, int | None]:
    """``(code, error_subcode)`` from a Graph API error body, if present."""
    if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
        return None, None
    err = body["error"]
    code = err.get("code")
    sub = err.get("error_subcode")
    return (code if isinstance(code, int) else None, sub if isinstance(sub, int) else None)


def classify(status: int, code: int | None) -> tuple[str, bool]:
    """Map a Graph API failure to ``(our error code, retryable)``."""
    if code in _TOKEN_CODES or status == 401:
        return "facebook_token_invalid", False
    if code in _RATE_LIMIT_CODES or status == 429:
        return "facebook_rate_limited", True
    if code in _TRANSIENT_CODES or status >= 500:
        return "facebook_unavailable", True
    if code in _PERMISSION_CODES or status == 403:
        return "facebook_permission_denied", False
    if code in _VIDEO_FORMAT_CODES:
        return "facebook_video_rejected", False
    if code == 100 and status == 404:
        return "facebook_page_not_found", False
    return "facebook_rejected", False


class GraphClient:
    """Small Graph API client with retries for requests that are safe to repeat."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        graph_base: str = GRAPH_BASE,
        video_base: str = VIDEO_BASE,
        version: str = GRAPH_VERSION,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._graph = f"{graph_base}/{version}"
        self._video = f"{video_base}/{version}"
        self._max_retries = max_retries
        self._sleep = sleep

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Call a Graph endpoint that does not upload a file; retried on transient errors."""
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._client.request(method, f"{self._graph}/{path}", **kwargs)
            except httpx.TimeoutException as exc:
                if method == "GET" and attempt <= self._max_retries:
                    self._sleep(self._backoff(attempt))
                    continue
                raise PublishError("network_error", retryable=True) from exc
            except (httpx.ConnectError, httpx.PoolTimeout) as exc:
                if attempt > self._max_retries:
                    raise PublishError("network_error", retryable=True) from exc
                self._sleep(self._backoff(attempt))
                continue
            except httpx.TransportError as exc:
                raise PublishError("network_error", retryable=True) from exc
            try:
                return self._result(resp)
            except PublishError as exc:
                if exc.retryable and attempt <= self._max_retries:
                    self._sleep(self._backoff(attempt))
                    continue
                raise

    def upload(self, path: str, data: dict[str, str], file: Path) -> dict[str, Any]:
        """Send a video in one multipart request.

        Only a failure to connect is retried (nothing was sent yet). Once the file is on
        its way, a timeout or a broken connection means "maybe posted": no retry.
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                with file.open("rb") as fh:
                    resp = self._client.post(
                        f"{self._video}/{path}",
                        data=data,
                        files={"source": (file.name, fh, "video/mp4")},
                    )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                if attempt > self._max_retries:
                    raise PublishError("network_error", retryable=True) from exc
                self._sleep(self._backoff(attempt))
                continue
            except httpx.TransportError as exc:
                raise PublishError("facebook_timeout_uncertain") from exc
            if resp.status_code >= 500 and graph_error(_json(resp))[0] is None:
                # A gateway error says nothing about whether the post was created.
                raise PublishError("facebook_timeout_uncertain", status=resp.status_code)
            return self._result(resp)

    @staticmethod
    def _result(resp: httpx.Response) -> dict[str, Any]:
        body = _json(resp)
        if resp.is_success and isinstance(body, dict) and "error" not in body:
            return body
        code, subcode = graph_error(body)
        our_code, retryable = classify(resp.status_code, code)
        params: dict[str, Any] = {"status": resp.status_code}
        if code is not None:
            params["fb_code"] = code
        if subcode is not None:
            params["fb_subcode"] = subcode
        raise PublishError(our_code, retryable=retryable, **params)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return float(min(2 ** (attempt - 1), 30))


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


class FacebookPageAdapter(PlatformAdapter):
    platform: ClassVar[Platform] = Platform.FACEBOOK
    supported_formats: ClassVar[tuple[PostFormat, ...]] = (PostFormat.VIDEO,)
    # Facebook accepts scheduled posts 10 minutes to 30 days ahead.
    min_schedule_lead: ClassVar[timedelta] = timedelta(minutes=10)
    max_schedule_ahead: ClassVar[timedelta] = timedelta(days=30)

    def __init__(
        self,
        page_id: str,
        page_token: str,
        *,
        graph: GraphClient | None = None,
        on_token_invalid: Callable[[], None] | None = None,
    ) -> None:
        self._page_id = page_id
        self._token = page_token
        self._graph = graph or GraphClient()
        self._on_token_invalid = on_token_invalid

    @property
    def supports_native_scheduling(self) -> bool:
        return True

    def _guard(self, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return call()
        except PublishError as exc:
            if exc.code == "facebook_token_invalid" and self._on_token_invalid is not None:
                self._on_token_invalid()
            raise

    def authenticate(self) -> None:
        """Check that the page token still works."""
        self._guard(
            lambda: self._graph.request(
                "GET", self._page_id, params={"fields": "id", "access_token": self._token}
            )
        )

    def validate(self, video: Video) -> ValidationResult:
        path = video.path
        if not path.is_file():
            return ValidationResult(ok=False, errors=["video_not_found"])
        errors: list[str] = []
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            errors.append("facebook_unsupported_format")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            errors.append("facebook_file_too_large")
        return ValidationResult(ok=not errors, errors=errors)

    def _check(self, publication: Publication) -> None:
        video = publication.video
        check = self.validate(video)
        if not check.ok:
            code = check.errors[0]
            params: dict[str, Any] = {}
            if code == "facebook_file_too_large":
                params = {
                    "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
                    "size_mb": round(video.path.stat().st_size / (1024 * 1024), 1),
                }
            raise PublishError(code, **params)
        self.check_text(publication)

    def check_text(self, publication: Publication) -> None:
        title = publication.text.title.strip()
        if len(title) > MAX_TITLE:
            raise PublishError("facebook_title_too_long", max=MAX_TITLE, length=len(title))

    def _form(self, publication: Publication) -> dict[str, str]:
        data = {"access_token": self._token}
        title = publication.text.title.strip()
        if title:
            data["title"] = title
        description = build_description(publication.text)
        if description:
            data["description"] = description
        return data

    def _send(self, publication: Publication, data: dict[str, str]) -> PublishResult:
        body = self._guard(
            lambda: self._graph.upload(f"{self._page_id}/videos", data, publication.video.path)
        )
        video_id = str(body.get("id", ""))
        if not video_id:
            raise PublishError("facebook_rejected")
        return PublishResult(external_id=video_id, url=video_url(self._page_id, video_id))

    def publish(self, publication: Publication) -> PublishResult:
        self._check(publication)
        return self._send(publication, self._form(publication))

    def schedule(self, publication: Publication, at: datetime) -> PublishResult:
        self._check(publication)
        if at.tzinfo is None:
            raise PublishError("schedule_timezone_required")
        data = self._form(publication)
        data["published"] = "false"
        data["scheduled_publish_time"] = str(int(at.timestamp()))
        return self._send(publication, data)

    def cancel_scheduled(self, external_id: str) -> None:
        """Delete the unpublished video, so it never goes out."""
        try:
            self._guard(
                lambda: self._graph.request(
                    "DELETE", external_id, params={"access_token": self._token}
                )
            )
        except PublishError as exc:
            if exc.code == "facebook_page_not_found":
                return  # already gone (deleted on Facebook by hand)
            raise
