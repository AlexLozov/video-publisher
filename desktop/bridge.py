"""Bridge between the pywebview window and the React UI.

* ``DesktopApi`` is exposed to JavaScript as ``window.pywebview.api``: ``pick_video()``
  opens the native Windows "Open file" dialog and returns the full path (no copy).
* Drag-and-drop: browsers hide the real path of a dropped file, but pywebview adds
  ``pywebviewFullPath``. ``on_drop`` reads it and forwards it to the UI as a
  ``vp:file-dropped`` DOM event.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

# pywebview accepts only letters, digits, "_" and spaces in a filter label:
# "Видео / Video" (with a slash) raised ValueError and the dialog never opened.
VIDEO_FILE_TYPES = (
    "Video (*.mp4;*.mov;*.m4v;*.mkv;*.avi;*.webm)",
    "All files (*.*)",
)


class FileDialogWindow(Protocol):
    def create_file_dialog(
        self,
        dialog_type: int = ...,
        directory: str = ...,
        allow_multiple: bool = ...,
        save_filename: str = ...,
        file_types: Sequence[str] = ...,
    ) -> Sequence[str] | None: ...

    def evaluate_js(self, script: str) -> Any: ...


class DesktopApi:
    """Methods callable from JavaScript. Attribute names starting with _ are not exposed."""

    def __init__(self, open_dialog_type: int) -> None:
        self._window: FileDialogWindow | None = None
        self._open_dialog_type = open_dialog_type

    def attach(self, window: FileDialogWindow) -> None:
        self._window = window

    def pick_video(self) -> str | None:
        if self._window is None:
            return None
        result = self._window.create_file_dialog(
            self._open_dialog_type, allow_multiple=False, file_types=VIDEO_FILE_TYPES
        )
        if not result:
            return None  # dialog cancelled
        return str(result[0])


def dropped_path(event: Mapping[str, Any]) -> str | None:
    """Full path of the first dropped file, if pywebview provided it."""
    files = (event.get("dataTransfer") or {}).get("files") or []
    for file in files:
        path = file.get("pywebviewFullPath") if isinstance(file, Mapping) else None
        if path:
            return str(path)
    return None


def dispatch_script(path: str) -> str:
    """JS that hands a dropped path to the UI. ``json.dumps`` escapes quotes and backslashes."""
    detail = json.dumps(path)
    return f"window.dispatchEvent(new CustomEvent('vp:file-dropped', {{detail: {detail}}}))"


def make_drop_handler(window: FileDialogWindow) -> Callable[[Mapping[str, Any]], None]:
    def on_drop(event: Mapping[str, Any]) -> None:
        path = dropped_path(event)
        if path:
            window.evaluate_js(dispatch_script(path))

    return on_drop
