"""Logging in with Facebook to get a page token (Settings → Facebook → "Log in").

States: ``logged_out`` → ``waiting`` (browser open) → (``choose_page`` if the user manages
several pages) → ``logged_in``.

Flow (OAuth "code" flow, the user's own Meta app):

1. ``start()`` opens a small listener on ``http://localhost:<port>/facebook/callback`` and
   opens the Facebook login dialog in the system browser.
2. Facebook redirects the browser to the listener with ``code`` and ``state``. The code is
   exchanged for a user token (needs the App Secret), that one for a long-lived user token,
   and ``/me/accounts`` lists the pages with their page tokens. A page token taken from a
   long-lived user token does not expire.
3. The chosen page token goes to the secret store (``facebook_page_token``), the page id
   and name to settings. Other page tokens and the user tokens are kept in memory only
   while the choice is pending, then dropped.

Errors from the callback (user cancelled, wrong secret...) are kept as ``error`` in the
status, because they happen in the listener thread, not in an API call.
"""

from __future__ import annotations

import html
import secrets
import socket
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from pydantic import BaseModel, Field

from video_publisher.auth.secret_store import SecretName
from video_publisher.platforms.errors import PublishError
from video_publisher.platforms.facebook_page import GRAPH_VERSION, GraphClient
from video_publisher.user_settings import SettingsService

DIALOG_BASE = "https://www.facebook.com"
CALLBACK_PATH = "/facebook/callback"
SCOPES = ("pages_show_list", "pages_read_engagement", "pages_manage_posts")

# The page the browser shows after the redirect. It is outside the app window, so it
# carries its own short texts in every UI language (keys must match, see tests).
CALLBACK_TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "title": "Video Publisher",
        "ok": (
            "Вход выполнен. Вернитесь в приложение Video Publisher, окно браузера можно закрыть."
        ),
        "failed": (
            "Войти не удалось. Вернитесь в приложение Video Publisher: там написано, что случилось."
        ),
    },
    "en": {
        "title": "Video Publisher",
        "ok": "You are logged in. Return to Video Publisher; you can close this browser tab.",
        "failed": "Login failed. Return to Video Publisher to see what went wrong.",
    },
}


class FacebookLoginState(StrEnum):
    LOGGED_OUT = "logged_out"
    WAITING = "waiting"
    CHOOSE_PAGE = "choose_page"
    LOGGED_IN = "logged_in"


class PageInfo(BaseModel):
    id: str
    name: str


class FacebookLoginStatus(BaseModel):
    state: FacebookLoginState
    page: PageInfo | None = None
    # Pages to choose from (``choose_page`` only). Tokens are never part of the status.
    pages: list[PageInfo] = Field(default_factory=list)
    # The address to add to "Valid OAuth Redirect URIs" in the Meta app.
    redirect_uri: str
    # Why the last login attempt failed, e.g. ``facebook_login_denied``.
    error: str | None = None


@dataclass
class _Page:
    id: str
    name: str
    token: str


class _ExclusiveServer(ThreadingHTTPServer):
    """Fails when the port is taken. The stock server sets SO_REUSEADDR, which on Windows
    lets a second program bind the same port silently, so the redirect could go elsewhere.
    """

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:  # Windows only
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


class CallbackListener:
    """A tiny HTTP server for the OAuth redirect; runs until stopped."""

    def __init__(
        self,
        port: int,
        handle: Callable[[dict[str, str]], bool],
        texts: Callable[[], dict[str, str]],
    ) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urlparse(self.path)
                if url.path != CALLBACK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                ok = handle(query)
                t = texts()
                message = html.escape(t["ok"] if ok else t["failed"])
                page = (
                    "<!doctype html><meta charset='utf-8'>"
                    f"<title>{html.escape(t['title'])}</title>"
                    "<body style='font-family:sans-serif;padding:3em;font-size:18px'>"
                    f"<p>{message}</p></body>"
                )
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass  # the query string holds the OAuth code: never log it

        try:
            self._server = _ExclusiveServer(("127.0.0.1", port), Handler)
        except OSError as exc:
            raise PublishError("facebook_port_busy", port=port) from exc
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fb-callback", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


ListenerFactory = Callable[
    [int, Callable[[dict[str, str]], bool], Callable[[], dict[str, str]]], CallbackListener
]


class FacebookLoginService:
    def __init__(
        self,
        settings: SettingsService,
        *,
        redirect_port: int,
        graph: GraphClient | None = None,
        open_browser: Callable[[str], object] = webbrowser.open,
        listener_factory: ListenerFactory = CallbackListener,
        dialog_base: str = DIALOG_BASE,
    ) -> None:
        self._settings = settings
        self._port = redirect_port
        self._graph = graph or GraphClient()
        self._open = open_browser
        self._listen = listener_factory
        self._dialog = dialog_base
        self._lock = threading.Lock()
        self._listener: CallbackListener | None = None
        self._state_token: str | None = None
        self._pages: list[_Page] = []
        self._error: str | None = None
        self._redirect_uri = self._uri(redirect_port)

    @staticmethod
    def _uri(port: int) -> str:
        return f"http://localhost:{port}{CALLBACK_PATH}"

    # --- state ------------------------------------------------------------------------

    def status(self) -> FacebookLoginStatus:
        fb = self._settings.get().facebook
        if self._settings.get_secret(SecretName.FACEBOOK_PAGE_TOKEN) and fb.page_id:
            return FacebookLoginStatus(
                state=FacebookLoginState.LOGGED_IN,
                page=PageInfo(id=fb.page_id, name=fb.page_name or fb.page_id),
                redirect_uri=self._redirect_uri,
            )
        with self._lock:
            if self._pages:
                state = FacebookLoginState.CHOOSE_PAGE
            elif self._listener is not None:
                state = FacebookLoginState.WAITING
            else:
                state = FacebookLoginState.LOGGED_OUT
            return FacebookLoginStatus(
                state=state,
                pages=[PageInfo(id=p.id, name=p.name) for p in self._pages],
                redirect_uri=self._redirect_uri,
                error=self._error,
            )

    def _credentials(self) -> tuple[str, str]:
        app_id = self._settings.get().facebook.app_id
        secret = self._settings.get_secret(SecretName.FACEBOOK_APP_SECRET)
        missing = [n for n, v in (("app_id", app_id), ("app_secret", secret)) if not v]
        if missing:
            raise PublishError("facebook_app_not_configured", missing=missing)
        assert app_id is not None and secret is not None
        return app_id, secret

    # --- steps ------------------------------------------------------------------------

    def start(self) -> FacebookLoginStatus:
        """Open the Facebook login dialog in the browser."""
        app_id, _ = self._credentials()
        if self.status().state is FacebookLoginState.LOGGED_IN:
            raise PublishError("facebook_already_logged_in")
        with self._lock:
            self._stop_listener()
            self._pages = []
            self._error = None
            self._listener = self._listen(self._port, self._on_callback, self._texts)
            self._redirect_uri = self._uri(self._listener.port)
            self._state_token = secrets.token_urlsafe(24)
            url = self.dialog_url(app_id, self._state_token)
        self._open(url)
        return self.status()

    def dialog_url(self, app_id: str, state: str) -> str:
        query = urlencode(
            {
                "client_id": app_id,
                "redirect_uri": self._redirect_uri,
                "state": state,
                "response_type": "code",
                "scope": ",".join(SCOPES),
            }
        )
        return f"{self._dialog}/{GRAPH_VERSION}/dialog/oauth?{query}"

    def _texts(self) -> dict[str, str]:
        return CALLBACK_TEXTS.get(self._settings.get().language, CALLBACK_TEXTS["en"])

    def _on_callback(self, query: dict[str, str]) -> bool:
        """Handle the browser redirect. True if the login went through."""
        with self._lock:
            expected = self._state_token
        if expected is None or not secrets.compare_digest(query.get("state", ""), expected):
            return False  # stale tab or a forged request: ignore, keep waiting
        try:
            if "error" in query or "code" not in query:
                raise PublishError("facebook_login_denied")
            pages = self._exchange(query["code"])
        except PublishError as exc:
            with self._lock:
                self._error = exc.code
                self._finish_waiting()
            return False
        if len(pages) == 1:
            self._save(pages[0])
            with self._lock:
                self._finish_waiting()
            return True
        with self._lock:
            self._pages = pages
            self._finish_waiting()
        return True

    def _exchange(self, code: str) -> list[_Page]:
        app_id, secret = self._credentials()
        short = self._graph.request(
            "GET",
            "oauth/access_token",
            params={
                "client_id": app_id,
                "client_secret": secret,
                "redirect_uri": self._redirect_uri,
                "code": code,
            },
        )
        long = self._graph.request(
            "GET",
            "oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": secret,
                "fb_exchange_token": str(short.get("access_token", "")),
            },
        )
        accounts = self._graph.request(
            "GET",
            "me/accounts",
            params={
                "fields": "id,name,access_token",
                "limit": "100",
                "access_token": str(long.get("access_token", "")),
            },
        )
        pages = [
            _Page(id=str(p["id"]), name=str(p.get("name") or p["id"]), token=str(p["access_token"]))
            for p in accounts.get("data", [])
            if isinstance(p, dict) and p.get("id") and p.get("access_token")
        ]
        if not pages:
            raise PublishError("facebook_no_pages")
        return pages

    def select_page(self, page_id: str) -> FacebookLoginStatus:
        with self._lock:
            page = next((p for p in self._pages if p.id == page_id), None)
        if page is None:
            raise PublishError("facebook_page_not_offered")
        self._save(page)
        with self._lock:
            self._pages = []
        return self.status()

    def _save(self, page: _Page) -> None:
        self._settings.set_secret(SecretName.FACEBOOK_PAGE_TOKEN, page.token)
        self._settings.set_facebook_page(page.id, page.name)

    def cancel(self) -> FacebookLoginStatus:
        with self._lock:
            self._stop_listener()
            self._pages = []
            self._error = None
        return self.status()

    def logout(self) -> FacebookLoginStatus:
        """Forget the page token and the page (the Meta app keys stay)."""
        self.cancel()
        self._settings.delete_secret(SecretName.FACEBOOK_PAGE_TOKEN)
        self._settings.set_facebook_page(None, None)
        return self.status()

    def shutdown(self) -> None:
        with self._lock:
            self._stop_listener()

    # Called with the lock held.
    def _finish_waiting(self) -> None:
        self._state_token = None
        listener = self._listener
        self._listener = None
        if listener is not None:
            # Stopping from the handler thread would wait for itself: do it on the side.
            threading.Thread(target=listener.stop, daemon=True).start()

    def _stop_listener(self) -> None:
        self._state_token = None
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
