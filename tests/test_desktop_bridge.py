import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop"))

from bridge import (
    CSV_FILE_TYPES,
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


@pytest.mark.parametrize("file_type", (*VIDEO_FILE_TYPES, *CSV_FILE_TYPES))
def test_file_types_pass_pywebview_validation(file_type: str) -> None:
    # Regression: a "/" in the label made pywebview raise ValueError, so the button did nothing.
    from webview.util import parse_file_type

    parse_file_type(file_type)


SAVE = 30
BOM = chr(0xFEFF)


@pytest.mark.parametrize("returned", ["{dir}/history.csv", ("{dir}/history.csv",), "{dir}/history"])
def test_save_csv_writes_utf8_with_bom(tmp_path: Path, returned: Any) -> None:
    def fill(s: str) -> str:
        return s.format(dir=tmp_path.as_posix())

    result = fill(returned) if isinstance(returned, str) else tuple(fill(r) for r in returned)
    window = FakeWindow(result)
    api = DesktopApi(open_dialog_type=OPEN, save_dialog_type=SAVE)
    api.attach(window)

    saved = api.save_csv("history.csv", BOM + "a;б\r\n1;2\r\n")

    assert saved == str(tmp_path / "history.csv")
    raw = (tmp_path / "history.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig") == "a;б\r\n1;2\r\n"
    assert window.dialog_calls[0]["type"] == SAVE
    assert window.dialog_calls[0]["file_types"] == CSV_FILE_TYPES


@pytest.mark.parametrize("result", [None, "", ()])
def test_save_csv_cancelled(result: Any) -> None:
    api = DesktopApi(open_dialog_type=OPEN, save_dialog_type=SAVE)
    api.attach(FakeWindow(result))
    assert api.save_csv("h.csv", "x") is None


def test_save_csv_before_window_attached() -> None:
    assert DesktopApi(open_dialog_type=OPEN).save_csv("h.csv", "x") is None


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
