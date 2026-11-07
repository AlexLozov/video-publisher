"""Desktop entry point: runs the API in a background thread and opens a pywebview window.

Usage (from the repository root, after ``npm run build`` in ``ui/``)::

    python desktop/run.py
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import uvicorn
import webview
from webview.dom import DOMEventHandler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "desktop"))

from bridge import DesktopApi, make_drop_handler  # noqa: E402
from video_publisher.api import create_app  # noqa: E402
from video_publisher.config import get_settings  # noqa: E402

UI_DIST = ROOT / "ui" / "dist"


def find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        port: int = sock.getsockname()[1]
        return port


def start_server(host: str, port: int) -> uvicorn.Server:
    settings = get_settings()
    ui_dir = settings.ui_dist_dir or UI_DIST
    app = create_app(ui_dist_dir=ui_dir)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("API server did not start within 10 seconds")
        time.sleep(0.05)
    return server


def main() -> None:
    if not (UI_DIST / "index.html").is_file():
        print(f"UI is not built: {UI_DIST} is missing. Run `npm run build` in ui/ first.")
        sys.exit(1)

    host = "127.0.0.1"
    port = find_free_port(host)
    server = start_server(host, port)

    api = DesktopApi(
        open_dialog_type=webview.FileDialog.OPEN, save_dialog_type=webview.FileDialog.SAVE
    )
    window = webview.create_window(
        "Video Publisher", f"http://{host}:{port}/", width=1100, height=820, js_api=api
    )
    assert window is not None
    api.attach(window)

    def bind_drag_and_drop(win: webview.Window) -> None:
        # Prevent the browser from opening the dropped file; read its full path instead.
        swallow = DOMEventHandler(lambda _e: None, prevent_default=True, stop_propagation=True)
        on_drop = DOMEventHandler(
            make_drop_handler(win), prevent_default=True, stop_propagation=True
        )
        events = win.dom.document.events
        events.dragover += cast(Callable[..., Any], swallow)
        events.drop += cast(Callable[..., Any], on_drop)

    webview.start(bind_drag_and_drop, (window,))

    server.should_exit = True


if __name__ == "__main__":
    main()
