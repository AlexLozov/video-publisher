"""Logging in with Google to publish on YouTube (Settings → YouTube → "Log in").

States: ``logged_out`` → ``waiting`` (browser open) → ``logged_in``.

Flow (OAuth for desktop apps, the user's own Google Cloud project, "Desktop app" client):

1. ``start()`` opens a listener on ``http://127.0.0.1:<free port>/`` and opens Google's
   consent page in the system browser. Desktop clients accept any loopback port, so
   nothing has to be registered in Google Cloud. PKCE protects the code exchange.
2. Google redirects the browser to the listener with ``code`` and ``state``. The code is
   exchanged for tokens; the refresh token goes to the secret store
   (``youtube_refresh_token``), the channel id and title (``channels.list?mine=true``) to
   settings. The access token is used once and dropped.

Errors from the callback (user cancelled, unticked the YouTube permission, no channel...)
are kept as ``error`` in the status, because they happen in the listener thread.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import webbrowser
from collections.abc import Callable
from enum import StrEnum
from urllib.parse import urlencode

from pydantic import BaseModel

from video_publisher.auth.oauth_callback import CALLBACK_TEXTS, CallbackListener
from video_publisher.auth.secret_store import SecretName
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.youtube import API_BASE, GoogleClient
from video_publisher.user_settings import SettingsService

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CALLBACK_PATH = "/"
# One scope covers upload, reading the channel and deleting a scheduled video.
SCOPE = "https://www.googleapis.com/auth/youtube"


class YouTubeLoginState(StrEnum):
    LOGGED_OUT = "logged_out"
    WAITING = "waiting"
    LOGGED_IN = "logged_in"


class ChannelInfo(BaseModel):
    id: str
    title: str


class YouTubeLoginStatus(BaseModel):
    state: YouTubeLoginState
    channel: ChannelInfo | None = None
    # Why the last login attempt failed, e.g. ``youtube_login_denied``.
    error: str | None = None


ListenerFactory = Callable[
    [int, Callable[[dict[str, str]], bool], Callable[[], dict[str, str]]], CallbackListener
]


def _google_listener(
    port: int, handle: Callable[[dict[str, str]], bool], texts: Callable[[], dict[str, str]]
) -> CallbackListener:
    return CallbackListener(port, handle, texts, path=CALLBACK_PATH, busy_code="youtube_port_busy")


def pkce_pair() -> tuple[str, str]:
    """``(verifier, S256 challenge)`` for the authorization code exchange."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class YouTubeLoginService:
    def __init__(
        self,
        settings: SettingsService,
        *,
        google: GoogleClient | None = None,
        open_browser: Callable[[str], object] = webbrowser.open,
        listener_factory: ListenerFactory = _google_listener,
        auth_url: str = AUTH_URL,
    ) -> None:
        self._settings = settings
        self._google = google or GoogleClient()
        self._open = open_browser
        self._listen = listener_factory
        self._auth_url = auth_url
        self._lock = threading.Lock()
        self._listener: CallbackListener | None = None
        self._state_token: str | None = None
        self._verifier: str | None = None
        self._redirect_uri: str | None = None
        self._error: str | None = None

    # --- state ------------------------------------------------------------------------

    def status(self) -> YouTubeLoginStatus:
        yt = self._settings.get().youtube
        if self._settings.get_secret(SecretName.YOUTUBE_REFRESH_TOKEN) and yt.channel_id:
            return YouTubeLoginStatus(
                state=YouTubeLoginState.LOGGED_IN,
                channel=ChannelInfo(id=yt.channel_id, title=yt.channel_title or yt.channel_id),
            )
        with self._lock:
            state = (
                YouTubeLoginState.WAITING
                if self._listener is not None
                else YouTubeLoginState.LOGGED_OUT
            )
            return YouTubeLoginStatus(state=state, error=self._error)

    def _credentials(self) -> tuple[str, str]:
        client_id = self._settings.get().youtube.client_id
        secret = self._settings.get_secret(SecretName.YOUTUBE_CLIENT_SECRET)
        missing = [n for n, v in (("client_id", client_id), ("client_secret", secret)) if not v]
        if missing:
            raise PublishError("youtube_app_not_configured", missing=missing)
        assert client_id is not None and secret is not None
        return client_id, secret

    # --- steps ------------------------------------------------------------------------

    def start(self) -> YouTubeLoginStatus:
        """Open Google's consent page in the browser."""
        client_id, _ = self._credentials()
        if self.status().state is YouTubeLoginState.LOGGED_IN:
            raise PublishError("youtube_already_logged_in")
        with self._lock:
            self._stop_listener()
            self._error = None
            self._listener = self._listen(0, self._on_callback, self._texts)
            self._redirect_uri = f"http://127.0.0.1:{self._listener.port}{CALLBACK_PATH}"
            self._state_token = secrets.token_urlsafe(24)
            self._verifier, challenge = pkce_pair()
            url = self.consent_url(client_id, self._state_token, challenge)
        self._open(url)
        return self.status()

    def consent_url(self, client_id: str, state: str, challenge: str) -> str:
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": self._redirect_uri or "",
                "response_type": "code",
                "scope": SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                # A refresh token every time, so a new login always replaces the old one.
                "access_type": "offline",
                "prompt": "consent select_account",
            }
        )
        return f"{self._auth_url}?{query}"

    def _texts(self) -> dict[str, str]:
        return CALLBACK_TEXTS.get(self._settings.get().language, CALLBACK_TEXTS["en"])

    def _on_callback(self, query: dict[str, str]) -> bool:
        """Handle the browser redirect. True if the login went through."""
        with self._lock:
            expected, verifier, redirect = self._state_token, self._verifier, self._redirect_uri
        if expected is None or not secrets.compare_digest(query.get("state", ""), expected):
            return False  # stale tab or a forged request: ignore, keep waiting
        try:
            if "error" in query or "code" not in query:
                raise PublishError("youtube_login_denied")
            assert verifier is not None and redirect is not None
            self._exchange(query["code"], verifier, redirect)
        except PublishError as exc:
            with self._lock:
                self._error = exc.code
                self._finish_waiting()
            return False
        with self._lock:
            self._finish_waiting()
        return True

    def _exchange(self, code: str, verifier: str, redirect_uri: str) -> None:
        client_id, secret = self._credentials()
        tokens = self._google.token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": secret,
                "code_verifier": verifier,
            }
        )
        # With granular consent the user may untick the YouTube permission.
        if SCOPE not in str(tokens.get("scope", "")).split():
            raise PublishError("youtube_scope_missing")
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise PublishError("youtube_no_refresh_token")
        resp = self._google.request(
            "GET",
            f"{API_BASE}/channels",
            str(tokens["access_token"]),
            params={"part": "snippet", "mine": "true"},
        )
        items = resp.json().get("items") or []
        if not items:
            raise PublishError("youtube_no_channel")
        channel = items[0]
        title = str((channel.get("snippet") or {}).get("title") or channel["id"])
        self._settings.set_secret(SecretName.YOUTUBE_REFRESH_TOKEN, str(refresh))
        self._settings.set_youtube_channel(str(channel["id"]), title)

    def cancel(self) -> YouTubeLoginStatus:
        with self._lock:
            self._stop_listener()
            self._error = None
        return self.status()

    def logout(self) -> YouTubeLoginStatus:
        """Forget the refresh token and the channel (the Google client keys stay)."""
        self.cancel()
        self._settings.delete_secret(SecretName.YOUTUBE_REFRESH_TOKEN)
        self._settings.set_youtube_channel(None, None)
        return self.status()

    def shutdown(self) -> None:
        with self._lock:
            self._stop_listener()

    # Called with the lock held.
    def _finish_waiting(self) -> None:
        self._state_token = None
        self._verifier = None
        listener = self._listener
        self._listener = None
        if listener is not None:
            # Stopping from the handler thread would wait for itself: do it on the side.
            threading.Thread(target=listener.stop, daemon=True).start()

    def _stop_listener(self) -> None:
        self._state_token = None
        self._verifier = None
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
