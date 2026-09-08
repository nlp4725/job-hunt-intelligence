"""
Converts a job's raw scraped posted_date text ("5 hours ago", "Reposted 2
days ago", "3 weeks ago") into an absolute datetime.

The anchor must be *when that text was read off LinkedIn* — a relative
string is meaningless otherwise, and rendering it on a dashboard viewed days
or weeks later reads as if it just happened. Callers pass
Job.posted_date_seen_at, which db/models.py guarantees is written only
alongside a write to Job.posted_date.

This used to anchor to Job.last_seen_at, which was wrong twice over:

  1. last_seen_at carried onupdate=utcnow, so *any* write to the row moved
     it. On 2026-09-08 a workplace_type backfill re-stamped 3181 rows in one
     commit and every one of their displayed post dates jumped forward with
     it — a Conquer AI ML Engineer role captured 2026-07-13 as "4 days ago"
     (really ~Jul 9, "2 months ago" on LinkedIn) rendered as Sep 4.
  2. Even without that, scraper/run_scrape.py bumps last_seen_at for
     already-detailed jobs *without* refetching their detail, so a re-seen
     listing's unchanged text got measured against a newer anchor. 443 rows
     had drifted this way, one by 43 days.

Both are the same mistake: last_seen_at answers "when did we last see this
listing?", not "when did we last read this text?". Keep them distinct.

Returns None on anything it can't read, including a missing anchor — callers
must handle that (backend/templates/index.html excludes jobs with no
posted_at from the dashboard entirely).

"Reposted" is stripped rather than treated specially — LinkedIn's own
relative-age text after "Reposted" already reflects the repost, which is the
best signal available; we don't have the original post date separately.
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
