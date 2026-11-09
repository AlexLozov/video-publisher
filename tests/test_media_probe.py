"""Video metadata via hachoir: duration, frame size and the phone rotation matrix."""

from pathlib import Path

import pytest

from video_publisher.media.probe import VideoInfo, probe

MEDIA = Path(__file__).parent / "media"


def test_landscape() -> None:
    info = probe(MEDIA / "landscape.mp4")
    assert info == VideoInfo(duration_seconds=4.0, width=320, height=180)
    assert info.is_youtube_short is False


def test_portrait_is_short() -> None:
    info = probe(MEDIA / "portrait.mp4")
    assert info is not None
    assert (info.width, info.height) == (180, 320)
    assert info.is_youtube_short is True


def test_phone_rotation_swaps_sides() -> None:
    # Stored as 320x180 with a "rotate 90°" matrix, as phones record portrait video.
    info = probe(MEDIA / "rotated.mp4")
    assert info is not None
    assert (info.width, info.height) == (180, 320)
    assert info.is_youtube_short is True


def test_not_a_video(tmp_path: Path) -> None:
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"\x00" * 1024)
    assert probe(junk) is None


def test_missing_file(tmp_path: Path) -> None:
    assert probe(tmp_path / "nope.mp4") is None


@pytest.mark.parametrize(
    ("info", "short"),
    [
        (VideoInfo(180.0, 1080, 1920), True),
        (VideoInfo(180.5, 1080, 1920), False),
        (VideoInfo(30.0, 1080, 1080), True),
        (VideoInfo(30.0, 1920, 1080), False),
        (VideoInfo(None, 1080, 1920), None),
        (VideoInfo(30.0, None, None), None),
    ],
)
def test_short_rule(info: VideoInfo, short: bool | None) -> None:
    assert info.is_youtube_short is short
