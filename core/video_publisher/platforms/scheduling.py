"""Checks for a requested publication time, shared by every platform."""

from datetime import datetime, timedelta

from video_publisher.platforms.errors import PublishError

# A post scheduled closer than this may already be in the past when the upload ends.
MIN_LEAD = timedelta(minutes=1)


def check_schedule_time(at: datetime, now: datetime, max_ahead: timedelta) -> None:
    """Raise a ``PublishError`` unless ``at`` is a timezone-aware time in the allowed window.

    ``now`` must be timezone-aware too; comparison works across time zones.
    """
    if at.tzinfo is None or at.utcoffset() is None:
        raise PublishError("schedule_timezone_required")
    if at < now + MIN_LEAD:
        raise PublishError("schedule_in_past", min_minutes=int(MIN_LEAD.total_seconds() // 60))
    if at > now + max_ahead:
        raise PublishError("schedule_too_far", max_days=max_ahead.days)
