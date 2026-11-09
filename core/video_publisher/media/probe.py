"""Reading a video's duration and frame size with hachoir (pure Python, no ffprobe).

Phones often store a portrait video as a landscape frame plus a "rotate by 90°" matrix in
the track header. hachoir reports the stored frame, so the matrix is checked here and the
sides are swapped for a quarter turn; otherwise a portrait phone video would look
landscape and would not be recognised as a YouTube Short.

Probing never fails the publication: if the file cannot be read, ``probe`` returns
``None`` and the platform decides without the metadata.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hachoir.core import config as hachoir_config
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser

# hachoir prints warnings about unusual but harmless padding to stdout.
hachoir_config.quiet = True

log = logging.getLogger(__name__)

# YouTube treats vertical or square videos up to 3 minutes as Shorts.
SHORTS_MAX_SECONDS = 180.0


@dataclass(frozen=True)
class VideoInfo:
    duration_seconds: float | None
    width: int | None
    height: int | None

    @property
    def is_vertical_or_square(self) -> bool | None:
        if not self.width or not self.height:
            return None
        return self.height >= self.width

    @property
    def is_youtube_short(self) -> bool | None:
        """True / False, or None when duration or frame size is unknown."""
        shape = self.is_vertical_or_square
        if shape is None or self.duration_seconds is None:
            return None
        return shape and self.duration_seconds <= SHORTS_MAX_SECONDS


def probe(path: Path) -> VideoInfo | None:
    """Duration and displayed frame size of a video file, or None if it cannot be read."""
    try:
        parser = createParser(str(path))
        if parser is None:
            return None
        with parser:
            meta = extractMetadata(parser)
            if meta is None:
                return None
            duration = meta.get("duration", None)
            width = meta.get("width", None)
            height = meta.get("height", None)
            quarter_turn = _has_quarter_turn(parser)
    except Exception:  # a broken file must never break publishing
        log.info("Could not read video metadata of %s", path.name, exc_info=True)
        return None
    seconds = duration.total_seconds() if duration is not None else None
    w = int(width) if width else None
    h = int(height) if height else None
    if quarter_turn and w and h:
        w, h = h, w
    return VideoInfo(duration_seconds=seconds, width=w, height=h)


def _int_part(node: Any, name: str) -> int | None:
    try:
        return int(node[name]["int_part"].value)
    except (KeyError, ValueError, TypeError):
        return None


def _has_quarter_turn(root: Any) -> bool:
    """True if the video track header rotates the frame by 90° or 270°.

    The matrix is ``[a b u; c d v; x y w]``. Without rotation ``a = d = ±1`` and
    ``b = c = 0``; for a quarter turn ``a = d = 0`` and ``b``, ``c`` are ±1.
    """
    for header in _track_headers(root):
        if not _int_part(header, "frame_size_width"):
            continue  # an audio track
        a, b = _int_part(header, "geom_a"), _int_part(header, "geom_b")
        return a == 0 and bool(b)
    return False


def _track_headers(node: Any, depth: int = 0) -> list[Any]:
    found: list[Any] = []
    if depth > 12:
        return found
    for field in node:
        if not field.is_field_set:
            continue
        if field.name == "track_hdr":
            found.append(field)
        elif field.name not in ("mdat", "media_data"):
            found.extend(_track_headers(field, depth + 1))
    return found
