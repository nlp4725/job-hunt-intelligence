"""Records and reports the per-page funnel for a manual screening session.

Record each page AS IT COMPLETES, as page:rendered:skipped:clicked tuples:

    ./venv/bin/python -m tests_and_eval.collection_report record \\
        --session 20260908-ai-engineer --keyword "ai engineer" --endpoint literal \\
        --pages 1:25:7:18

Per page, not batched at the end. A screening run dies mid-way often enough to
plan for it — the extension disconnects every 20-30 jobs and the renderer goes
unresponsive on long sessions — and a run that crashes is exactly the run whose
funnel you need to inspect. Recording at the end loses all of it precisely then.
The call is cheap next to the screenshots a page already costs.

`--pages` still accepts several at once (`1:25:7:18,2:25:6:19`) for backfilling
a session recorded on paper, but the live path is one page per call.

Report the session at the end:

    ./venv/bin/python -m tests_and_eval.collection_report report --session 20260908-ai-engineer

Why per page and not just a session total: a short page is the one collection
failure that leaves NO trace in the data afterwards. A job you never enumerated
produces no row, no null, no error — the DB looks perfectly healthy and you
simply never saw the listing. Comparing rendered against LinkedIn's fixed 25 is
the only place that miss is detectable, and only at the moment it happens.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from db.models import CollectionPage, Job, ScreeningResult  # noqa: E402
from db.session import SessionLocal  # noqa: E402

# LinkedIn serves 25 results per search page. The last page of a result set is
# legitimately short; any earlier one is an incomplete scroll.
EXPECTED_PER_PAGE = 25


def _parse_pages(spec):
    """'1:25:7:18,2:25:6:19' -> [(1,25,7,18), (2,25,6,19)]"""
    out = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            page, rendered, skipped, clicked = (int(x) for x in chunk.split(":"))
        except ValueError:
            raise SystemExit(f"bad --pages entry {chunk!r}; expected page:rendered:skipped:clicked")
        out.append((page, rendered, skipped, clicked))
    if not out:
        raise SystemExit("--pages parsed to nothing")
    return out


def record(args):
    session = SessionLocal()
    pages = _parse_pages(args.pages)
    problems = 0
    for i, (page, rendered, skipped, clicked) in enumerate(pages):
        session.add(CollectionPage(
            session_id=args.session, keyword=args.keyword, endpoint=args.endpoint,
            page=page, rendered=rendered, skipped=skipped, clicked=clicked,
        ))
        notes = []
        if rendered != skipped + clicked:
            notes.append(f"{rendered - skipped - clicked} card(s) unaccounted for")
        # Warn on ANY short page here. `record` cannot know whether this is the
        # last page of the result set (that is the one place a short page is
        # legitimate), and it sees one page at a time by design. The operator
        # can judge it in the moment; `report` applies the last-page exemption
        # afterwards, when it can actually tell which page was last.
        if rendered < EXPECTED_PER_PAGE:
            notes.append(f"only {rendered}/{EXPECTED_PER_PAGE} rendered — page not fully "
                         "scrolled (ignore if this is the last page of the result set)")
        if notes:
            problems += 1
            print(f"  WARN page {page}: {'; '.join(notes)}")
    session.commit()
    total = sum(p[1] for p in pages)
    print(f"  recorded {len(pages)} page(s), {total} cards" + ("" if problems else " — all balanced"))
    return 1 if problems else 0


def report(args):
    session = SessionLocal()
    pages = (
        session.query(CollectionPage)
        .filter(CollectionPage.session_id == args.session)
        .order_by(CollectionPage.page)
        .all()
    )
    if not pages:
        print(f"no pages recorded for session {args.session!r}")
        return 1

    print(f"session {args.session}  ({pages[0].keyword}, {pages[0].endpoint or 'endpoint n/a'})\n")
    print(f"  {'page':>4} {'rendered':>9} {'skipped':>8} {'clicked':>8}   note")
    problems = 0
    for i, p in enumerate(pages):
        notes = []
        if p.rendered != p.skipped + p.clicked:
            notes.append(f"{p.rendered - p.skipped - p.clicked} unaccounted")
        # The final page is allowed to be short; an earlier one is not.
        if p.rendered < EXPECTED_PER_PAGE and i < len(pages) - 1:
            notes.append(f"short page ({p.rendered}/{EXPECTED_PER_PAGE})")
        problems += bool(notes)
        print(f"  {p.page:>4} {p.rendered:>9} {p.skipped:>8} {p.clicked:>8}   {'; '.join(notes)}")

    rendered = sum(p.rendered for p in pages)
    skipped = sum(p.skipped for p in pages)
    clicked = sum(p.clicked for p in pages)
    print(f"\n  TOTAL {rendered:>8} {skipped:>8} {clicked:>8}")

    # Reconcile what the session says it captured against what actually landed.
    # A clicked card that produced no ScreeningResult was captured and then lost
    # somewhere between the extension and the DB — the gap this catches is
    # exactly the one nobody notices, because both halves look fine alone.
    first, last = pages[0].recorded_at, pages[-1].recorded_at
    screened = (
        session.query(ScreeningResult)
        .filter(ScreeningResult.screened_at >= first, ScreeningResult.screened_at <= last)
        .count()
    )
    captured = session.query(Job).filter(Job.first_seen_at >= first, Job.first_seen_at <= last).count()
    print(f"\n  clicked {clicked}  ->  new job rows {captured}  ->  screened {screened}")
    if screened < clicked:
        print(f"  WARN {clicked - screened} clicked card(s) produced no screening result "
              "(cached re-visits are expected here; a large gap is not)")
        problems += 1
    print(f"\n{'FUNNEL OK' if not problems else str(problems) + ' page(s)/stage(s) need review'}")
    return 1 if problems else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--session", required=True)
    r.add_argument("--keyword", required=True)
    r.add_argument("--endpoint")
    r.add_argument("--pages", required=True,
                   help="comma-separated page:rendered:skipped:clicked, e.g. 1:25:7:18,2:25:6:19")
    r.set_defaults(func=record)
    q = sub.add_parser("report")
    q.add_argument("--session", required=True)
    q.set_defaults(func=report)
    args = p.parse_args()
    sys.exit(args.func(args))
