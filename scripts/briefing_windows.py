"""Fixed KST publication windows for the three daily IB brief editions.

Slots are defined by article publication time, not by how late the GitHub
scheduler starts. Window borders do not overlap.
"""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
EDITION_ORDER = ("0730", "1000", "1500")
EDITION_LABELS = {
    "0730": "07:30",
    "1000": "10:00",
    "1500": "15:00",
}
PER_EDITION_LIMIT = 2
MAX_STORIES_PER_CATEGORY = 6


def edition_for_run(now: datetime) -> str:
    """An early manual run prepares the next edition; delayed runs keep their slot."""
    local = now.astimezone(KST)
    minute = local.hour * 60 + local.minute
    if minute < 10 * 60:
        return "0730"
    if minute < 15 * 60:
        return "1000"
    return "1500"


def edition_window(now: datetime, edition: str):
    local = now.astimezone(KST)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if edition == "0730":
        start = (midnight - timedelta(days=1)).replace(hour=15)
        end = midnight.replace(hour=7, minute=30)
    elif edition == "1000":
        start = midnight.replace(hour=7, minute=30)
        end = midnight.replace(hour=10)
    elif edition == "1500":
        start = midnight.replace(hour=10)
        end = midnight.replace(hour=15)
    else:
        raise ValueError("Unknown briefing edition: " + str(edition))
    return start, end


def in_edition_window(published: datetime, now: datetime, edition: str) -> bool:
    start, end = edition_window(now, edition)
    published = published.astimezone(KST)
    if edition == "0730":
        return start <= published <= min(now.astimezone(KST), end)
    return start < published <= min(now.astimezone(KST), end)


def in_day_window(published: datetime, now: datetime) -> bool:
    first_start, _ = edition_window(now, "0730")
    _, last_end = edition_window(now, "1500")
    return first_start <= published.astimezone(KST) <= min(now.astimezone(KST), last_end)


def edition_for_published(published: datetime, now: datetime):
    for edition in EDITION_ORDER:
        if in_edition_window(published, now, edition):
            return edition
    return None
