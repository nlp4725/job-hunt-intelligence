"""Live progress of a screening run happening in another session.

    ./venv/bin/python -m tests_and_eval.progress          # one snapshot
    ./venv/bin/python -m tests_and_eval.progress --watch  # refresh every 30s

Reads the DB directly, so it works from any terminal while the run drives the
browser elsewhere. Everything here is written incrementally during the run
(pages at step 3, captures on every extension POST), which is exactly why it
is visible before the run finishes.
"""

import argparse
import pathlib
import sys
import time
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from db.models import CollectionPage, ExtractionEvent, Job, ScreeningResult, utcnow  # noqa: E402
from db.session import SessionLocal  # noqa: E402


def snapshot(hours=6):
    s = SessionLocal()
    now = utcnow().replace(tzinfo=None)
    since = now - timedelta(hours=hours)

    pages = (
        s.query(CollectionPage)
        .filter(CollectionPage.recorded_at >= since)
        .order_by(CollectionPage.page)
        .all()
    )
    jobs = s.query(Job).filter(Job.first_seen_at >= since).count()
    scored = s.query(ScreeningResult).filter(ScreeningResult.screened_at >= since).count()
    events = s.query(ExtractionEvent).filter(ExtractionEvent.captured_at >= since).all()

    print(f"[{now:%H:%M:%S}]  last {hours}h")
    if pages:
        planned = next((p.pages_planned for p in reversed(pages) if p.pages_planned), None)
        rendered = sum(p.rendered for p in pages)
        clicked = sum(p.clicked for p in pages)
        skipped = sum(p.skipped for p in pages)
        last = max(p.page for p in pages)
        target = f"/{planned}" if planned else ""
        idle = (now - max(p.recorded_at for p in pages)).total_seconds() / 60
        print(f"  pages   {len(pages)}{target} recorded, latest page {last}"
              f"   (last write {idle:.0f} min ago)")
        print(f"  cards   {rendered} rendered = {skipped} skipped + {clicked} clicked")
    else:
        print("  pages   none recorded yet")

    print(f"  saved   {jobs} new job rows, {scored} screened")

    if events:
        nulls = {}
        for e in events:
            for field, strategy in (e.strategies or {}).items():
                nulls.setdefault(field, [0, 0])
                nulls[field][1] += 1
                if strategy is None:
                    nulls[field][0] += 1
        broken = [f for f, (n, t) in nulls.items() if t >= 5 and n / t >= 0.8]
        print(f"  capture {len(events)} extractions"
              + (f"   FIELDS FAILING: {', '.join(broken)}" if broken else "   all fields resolving"))
    else:
        print("  capture no extractions yet")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--watch", action="store_true")
    p.add_argument("--hours", type=int, default=6)
    a = p.parse_args()
    while True:
        snapshot(a.hours)
        if not a.watch:
            break
        print()
        time.sleep(30)
