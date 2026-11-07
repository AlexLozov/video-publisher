import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop"))

from bridge import (
    VIDEO_FILE_TYPES,
    DesktopApi,
    dispatch_script,
    dropped_path,
    make_drop_handler,
)

OPEN = 10


class FakeWindow:
    def __init__(self, result: Sequence[str] | None = None) -> None:
        self.result = result
        self.dialog_calls: list[dict[str, Any]] = []
        self.scripts: list[str] = []

    def create_file_dialog(
        self,
        dialog_type: int = OPEN,
        directory: str = "",
        allow_multiple: bool = False,
        save_filename: str = "",
        file_types: Sequence[str] = (),
    ) -> Sequence[str] | None:
        self.dialog_calls.append(
            {"type": dialog_type, "multiple": allow_multiple, "file_types": file_types}
        )
        return self.result

    def evaluate_js(self, script: str) -> Any:
        self.scripts.append(script)


@pytest.mark.parametrize("file_type", VIDEO_FILE_TYPES)
def test_file_types_pass_pywebview_validation(file_type: str) -> None:
    # Regression: a "/" in the label made pywebview raise ValueError, so the button did nothing.
    from webview.util import parse_file_type

    parse_file_type(file_type)


def test_pick_video_returns_full_path() -> None:
    window = FakeWindow([r"C:\Videos\clip.mp4"])
    api = DesktopApi(open_dialog_type=OPEN)
    api.attach(window)
    assert api.pick_video() == r"C:\Videos\clip.mp4"
    assert window.dialog_calls == [
        {"type": OPEN, "multiple": False, "file_types": VIDEO_FILE_TYPES}
    ]


@pytest.mark.parametrize("result", [None, [], ()])
def test_pick_video_cancelled(result: Sequence[str] | None) -> None:
    api = DesktopApi(open_dialog_type=OPEN)
    api.attach(FakeWindow(result))
    assert api.pick_video() is None


def test_pick_video_before_window_attached() -> None:
    assert DesktopApi(open_dialog_type=OPEN).pick_video() is None


def test_file_types_are_valid_pywebview_filters() -> None:
    for spec in VIDEO_FILE_TYPES:
        assert spec.endswith(")") and "(" in spec and "*." in spec


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"dataTransfer": {"files": [{"pywebviewFullPath": r"C:\a.mp4"}]}}, r"C:\a.mp4"),
        (
            {"dataTransfer": {"files": [{"name": "x"}, {"pywebviewFullPath": "D:/b.mov"}]}},
            "D:/b.mov",
        ),
        ({"dataTransfer": {"files": [{"name": "no path"}]}}, None),
        ({"dataTransfer": {"files": []}}, None),
        ({"dataTransfer": None}, None),
        ({}, None),
        ({"dataTransfer": {"files": ["weird"]}}, None),
    ],
)
def test_dropped_path(event: dict[str, Any], expected: str | None) -> None:
    assert dropped_path(event) == expected


def test_dispatch_script_escapes_path() -> None:
    path = 'C:\\Users\\O\'Brien\\"clip".mp4'
    script = dispatch_script(path)
    assert "vp:file-dropped" in script
    detail = script.split("detail: ", 1)[1].rsplit("}", 1)[0]
    assert json.loads(detail) == path


def test_drop_handler_forwards_path_to_ui() -> None:
    window = FakeWindow()
    handler = make_drop_handler(window)
    handler({"dataTransfer": {"files": [{"pywebviewFullPath": r"C:\a.mp4"}]}})
    handler({"dataTransfer": {"files": []}})  # nothing to forward
    assert window.scripts == [dispatch_script(r"C:\a.mp4")]
