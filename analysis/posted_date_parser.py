"""
Converts a job's raw scraped posted_date text ("5 hours ago", "Reposted 2
days ago", "3 weeks ago") into an absolute datetime, anchored to
Job.first_seen_at (when the scraper actually collected the listing) rather
than "now" — the relative string is only meaningful relative to whenever it
was scraped, and displaying it as-is on a dashboard viewed days/weeks later
reads as if it just happened. "Reposted" is stripped rather than treated
specially — LinkedIn's own relative-age text after "Reposted" already
reflects the repost, which is the best signal available; we don't have the
original post date separately.
"""

import re
from datetime import datetime, timedelta

_UNIT_TO_TIMEDELTA = {
    "second": lambda n: timedelta(seconds=n),
    "minute": lambda n: timedelta(minutes=n),
    "hour": lambda n: timedelta(hours=n),
    "day": lambda n: timedelta(days=n),
    "week": lambda n: timedelta(weeks=n),
    "month": lambda n: timedelta(days=n * 30),  # approximate, no calendar precision needed here
}

_PATTERN = re.compile(
    r"(?:reposted\s+)?(\d+)\s+(second|minute|hour|day|week|month)s?\s+ago",
    re.IGNORECASE,
)


def parse_posted_date(posted_date_text: str | None, collected_at: datetime | None) -> datetime | None:
    if not posted_date_text or not collected_at:
        return None
    match = _PATTERN.search(posted_date_text)
    if not match:
        return None
    count = int(match.group(1))
    unit = match.group(2).lower()
    return collected_at - _UNIT_TO_TIMEDELTA[unit](count)
