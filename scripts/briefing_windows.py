"""One daily IB news edition; fixed 24-hour KST publication window.

The edition published after 07:30 KST covers articles first published strictly
after the previous day's 07:30 and through today's 07:30, inclusive.
Late GitHub jobs must not include news published after the fixed cutoff.
"""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
EDITION_ORDER = ("0730",)
EDITION_LABELS = {"0730": "07:30"}
PER_EDITION_LIMIT = 3
MAX_STORIES_PER_CATEGORY = 3


def edition_for_run(now: datetime) -> str:
    """Always the daily 07:30 edition, including delayed/manual runs."""
    return "0730"


def edition_window(now: datetime, edition: str = "0730"):
    if edition != "0730":
        raise ValueError("Only the daily 07:30 briefing is supported")
    local = now.astimezone(KST)
    end = local.replace(hour=7, minute=30, second=0, microsecond=0)
    return end - timedelta(days=1), end


def in_edition_window(published: datetime, now: datetime, edition: str = "0730") -> bool:
    start, end = edition_window(now, edition)
    published = published.astimezone(KST)
    return start < published <= min(end, now.astimezone(KST))


def in_day_window(published: datetime, now: datetime) -> bool:
    """A date's briefing is precisely its single 07:30 publication window."""
    return in_edition_window(published, now, "0730")


def edition_for_published(published: datetime, now: datetime):
    return "0730" if in_day_window(published, now) else None
