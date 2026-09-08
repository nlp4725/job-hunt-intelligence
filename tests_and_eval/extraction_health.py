"""Per-field extraction health for a screening session — the drift alarm.

Run at the end of a run (the linkedin-manual-screen skill's report step calls
this) and after any change to extension/content/extract.js:

    python -m tests_and_eval.extraction_health            # last 24h
    python -m tests_and_eval.extraction_health --hours 6
    python -m tests_and_eval.extraction_health --export   # write failure snapshots as fixtures

What to look at: not individual nulls — plenty of postings genuinely show no
applicant count — but a field's NULL RATE, and which strategy is winning. A
field that was 100% "pill-leaf" yesterday and is 100% null today is LinkedIn
having rebuilt the page, and it is visible here the same session instead of
ten thousand rows later.
"""

import argparse
import pathlib
import sys
from collections import Counter, defaultdict
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from db.models import ExtractionEvent, utcnow  # noqa: E402
from db.session import SessionLocal  # noqa: E402

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"

# A field null on more than this share of captures is treated as broken rather
# than merely sparse. Set from what the fields that DON'T break look like:
# applicant_stats is legitimately absent on a large minority of postings, while
# a genuinely broken field goes to ~100% (workplace_type hit 97% in Sept 2026).
ALARM_NULL_RATE = 0.80

# A break that started an hour into a long session is invisible in the window
# average — 30 broken captures after 40 healthy ones is only 43% null, well
# under the alarm. So the trailing window is checked independently: what
# matters mid-session is whether the field is failing NOW, not on average.
TRAILING = 20


def report(hours=24, export=False):
    session = SessionLocal()
    since = utcnow() - timedelta(hours=hours)
    events = session.query(ExtractionEvent).filter(ExtractionEvent.captured_at >= since).all()
    if not events:
        print(f"no captures in the last {hours}h — nothing to report")
        return 0

    events.sort(key=lambda e: e.captured_at)
    per_field = defaultdict(Counter)
    for event in events:
        for field, strategy in (event.strategies or {}).items():
            per_field[field][strategy or "NULL"] += 1

    recent = defaultdict(Counter)
    for event in events[-TRAILING:]:
        for field, strategy in (event.strategies or {}).items():
            recent[field][strategy or "NULL"] += 1

    print(f"{len(events)} captures in the last {hours}h\n")
    alarms = []
    for field in sorted(per_field):
        counts = per_field[field]
        total = sum(counts.values())
        nulls = counts.get("NULL", 0)
        rate = nulls / total
        won = ", ".join(f"{n} {name}" for name, n in counts.most_common() if name != "NULL")
        r_total = sum(recent[field].values())
        r_rate = recent[field].get("NULL", 0) / r_total if r_total else 0.0
        broken = rate >= ALARM_NULL_RATE or (r_total >= 5 and r_rate >= ALARM_NULL_RATE)
        trail = f"   last {r_total}: {r_rate:5.1%} null" if r_total else ""
        flag = "  <-- BROKEN?" if broken else ""
        print(f"  {field:<16} {nulls:>4}/{total:<4} null ({rate:5.1%}){trail}   {won or '-'}{flag}")
        if broken:
            alarms.append(field)

    snapshots = [e for e in events if e.snapshot_html]
    print(f"\n{len(snapshots)} failure snapshot(s) stored")

    if export and snapshots:
        FIXTURE_DIR.mkdir(exist_ok=True)
        for event in snapshots:
            field = (event.failed_fields or ["unknown"])[0]
            name = f"{field}-{event.captured_at:%Y%m%d}-{event.job_id}.html"
            (FIXTURE_DIR / name).write_text(event.snapshot_html)
            print(f"  wrote fixtures/{name}")
        print("\nnow: node tests_and_eval/extraction_fixtures.js")

    if alarms:
        print(
            f"\nDRIFT: {', '.join(alarms)} failing on nearly every capture."
            "\nRepair loop: see .claude/skills/linkedin-manual-screen/SKILL.md,"
            " 'Repairing a field the extractor stopped finding'."
        )
    return 1 if alarms else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--export", action="store_true", help="write stored snapshots to tests_and_eval/fixtures/")
    args = parser.parse_args()
    sys.exit(report(hours=args.hours, export=args.export))
