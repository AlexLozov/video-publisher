from pathlib import Path

from video_publisher.domain import (
    Platform,
    PostFormat,
    Publication,
    PublicationStatus,
    PublishTarget,
    Video,
)


def test_platform_values() -> None:
    assert [p.value for p in Platform] == [
        "youtube",
        "tiktok",
        "instagram",
        "facebook",
        "telegram",
    ]


def test_post_format_values() -> None:
    assert {f.value for f in PostFormat} == {"short", "reels", "post", "video"}


def test_publication_defaults() -> None:
    pub = Publication(
        video=Video(path=Path("clip.mp4")),
        target=PublishTarget(platform=Platform.TIKTOK, format=PostFormat.VIDEO),
    )
    assert pub.status is PublicationStatus.DRAFT
    assert pub.scheduled_at is None
    assert pub.text.hashtags == []
