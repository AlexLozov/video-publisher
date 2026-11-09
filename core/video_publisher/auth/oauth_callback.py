"""A tiny local web server that receives the browser redirect at the end of an OAuth login.

Shared by the Facebook and Google (YouTube) logins. The page it shows in the browser is
outside the app window, so it carries its own short texts in every UI language.
"""

from __future__ import annotations

import html
import socket
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from video_publisher.platforms.errors import PublishError

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
        *,
        path: str,
        busy_code: str,
        host: str = "127.0.0.1",
    ) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urlparse(self.path)
                if url.path != path:
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
            self._server = _ExclusiveServer((host, port), Handler)
        except OSError as exc:
            raise PublishError(busy_code, port=port) from exc
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="oauth-callback", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
