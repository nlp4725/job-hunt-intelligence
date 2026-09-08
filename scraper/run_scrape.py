"""
Real orchestrator: scrape every configured search (see SEARCHES), dedup
against the DB, only fetch full detail for genuinely new jobs, extract
skills, and persist everything.

This is the script launchd will eventually call on a schedule. Run directly
for now:
    python scraper/run_scrape.py
"""

import queue
import sys
import threading
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # so `db.` / `analysis.` imports resolve when run directly

from selenium.common.exceptions import TimeoutException

from analysis.title_filter import is_relevant_title, is_too_senior_title
from db.job_writer import get_or_create_company, save_new_job
from db.models import Job, ScrapeRun, track_for_keyword, utcnow
from db.session import get_session, init_db
from judge.agency_blocklist import delete_agency_jobs, is_agency_company_name
from judge.eligibility import load_resumes, resume_for_screening
from judge.screening_run import run_screening, screen_one
from scraper.linkedin_scraper import (
    BroadMatchDegradedError,
    LinkedInBlockedError,
    PROFILE_DIR,
    TIME_RANGE_DAY,
    TIME_RANGE_MONTH,
    TIME_RANGE_WEEK,
    WORK_TYPE_REMOTE,
    build_driver,
    get_job_cards_on_page,
    jitter,
    maybe_take_a_break,
    scrape_job_detail,
)

# Narrowed to just "llm remote" (2026-08-17) — was ["llm", "ai engineer"],
# before that ["machine learning", "ai engineer", "ai scientist", "product
# manager in software"]. "ai engineer" dropped: its literal-match coverage
# turned out to heavily overlap with what "llm remote"'s broad match already
# surfaces (both return general AI/ML postings, not just literal-"llm"
# ones), so running both was mostly duplicate work. PM track remains paused
# (no PM keyword); existing pm-track jobs/resume/screening in the DB are
# untouched, this only affects what gets scraped going forward. See
# db.models.KEYWORD_TRACKS for the keyword->track mapping.
KEYWORDS = ["llm remote"]

# Remote, nationwide, no location filter — geo_id=None and the default
# work_type (WORK_TYPE_REMOTE) on every keyword. CA hybrid/on-site coverage
# was dropped; remote-only going forward.
#
# "llm remote" runs broad_match=True (2026-08-14, keyword changed 2026-08-17):
# literal-match against just the word "llm" was only turning up 4-5 real
# postings/day (verified — that's genuinely all LinkedIn has under that
# exact literal string for a remote+24h search), while LinkedIn's own
# related-term search endpoint returns 99+ for the same query by matching
# AI/ML/GenAI postings more broadly. The keyword itself is "llm remote", not
# "llm" — folding "remote" into the literal search text turned out to be a
# far more reliable way to get remote-only results than LinkedIn's own
# Remote filter chip (see get_job_cards_on_page's broad_match docstring and
# CONTEXT.md for why the filter chip was dropped as unreliable/non-
# deterministic even when it visibly "worked").
BROAD_MATCH_KEYWORDS = {"llm remote"}
SEARCHES = [(keyword, None, WORK_TYPE_REMOTE, None, keyword in BROAD_MATCH_KEYWORDS) for keyword in KEYWORDS]

LOG_DIR = Path(__file__).parent / "logs"


class _Tee:
    """Mirrors writes to multiple streams — used to send every print() both
    to the real terminal (so `python run_scrape.py` still shows live
    progress) and to a log file, without touching the print() calls
    scattered across this file and linkedin_scraper.py. Needed once launchd
    is calling this unattended and there's no terminal to watch."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


def setup_file_logging(mode: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"run_{mode}_{utcnow().strftime('%Y%m%d_%H%M%S')}.log"
    log_file = open(log_path, "a")
    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)
    print(f"Logging this run to {log_path}")

# LinkedIn doesn't return an empty page once you've paged past its real
# result set — it keeps serving pages (often repeats), so "stop on empty
# page" alone can run away for hundreds of pages past a small time-scoped
# result set. Two consecutive pages where every id already needs no new
# detail work is a reliable "paged past everything new" signal; the page
# caps are a hard backstop in case that signal doesn't fire.
CONSECUTIVE_EMPTY_PAGES_LIMIT = 2
MAX_PAGES_DAILY = 60       # TIME_RANGE_DAY pool is small (hundreds of jobs) — 60*25 = 1500 is already generous
MAX_PAGES_WEEK = 100       # TIME_RANGE_WEEK pool is bigger than a day but smaller than a month — 100*25 = 2500
MAX_PAGES_BACKFILL = 200   # TIME_RANGE_MONTH pool is much larger, but still needs a hard ceiling

SCREENING_WORKERS = 8  # same concurrency level as judge/screening_run.py's own batch pool — see that file's docstring on why 8


def _screening_worker(job_queue: queue.Queue) -> None:
    """Runs for the whole scrape, on its own thread — pulls one (job_id,
    resume_content) pair at a time off the queue and screens it immediately,
    so scoring keeps pace with the scraper instead of only starting once
    every keyword is done. A pool of these (see main()) is what makes this
    concurrent: whichever thread is free grabs the next item, same pattern
    as judge/screening_run.py's own worker pool, just fed live instead of
    from one big upfront batch query.

    The scraper and these screening workers commit to the same SQLite file
    from different threads at once — no different in kind from
    judge/screening_run.py's existing 8-way concurrent screening (each
    screen_one() call already opens its own session), just now overlapping
    with the scraper's own commits too. SQLite's default busy-timeout
    absorbs the resulting contention; screen_one() already logs rather than
    raises on failure, so a lock timeout here is a skipped job, not a
    crashed run.

    None is the shutdown sentinel — one is pushed per worker once the
    scraper has no more jobs to enqueue (see main())."""
    while True:
        item = job_queue.get()
        try:
            if item is None:
                return
            job_id, resume_content = item
            _, error = screen_one(job_id, resume_content)
            if error:
                print(f"  [screening] job {job_id} FAILED: {error}")
        finally:
            job_queue.task_done()


def create_placeholder_job(session, keyword: str, track: str, job_id: str) -> None:
    """Phase 1 (daily run): persist a bare id-only row the moment a job is
    discovered, before its content is ever fetched. This is what lets phase
    1's own dedup recognize the same job reappearing on a later page within
    this run (LinkedIn's feed drifting mid-scan) as already handled, and
    lets a killed process resume from where it left off instead of losing
    everything phase 1 found — detail_fetched stays False until phase 2
    completes it."""
    job = Job(
        job_id=job_id,
        url=f"https://www.linkedin.com/jobs/view/{job_id}/",
        keyword_matched=keyword,
        track=track,
        detail_fetched=False,
    )
    session.add(job)
    session.commit()


REPOST_GAP_DAYS = 7  # an already-known job reappearing in a TIME_RANGE_DAY (f_TPR=r86400, "past 24h") search after this long a gap since last_seen_at can only mean LinkedIn just reposted/rebumped it — that's the sole way it re-qualifies for a 24h-window search. A shorter gap is more likely routine day-to-day scrape overlap than a genuine new repost cycle, so it's not counted.


def dedup_page(session, job_ids: list[str]) -> tuple[list[str], set[str]]:
    """One batch query for the whole page instead of 25 individual ones.
    Splits job_ids into "already have full detail" (bump last_seen_at, check
    for a repost) vs "still needs detail" — either a genuinely new id (no
    row at all yet) or a placeholder row left by a run that was interrupted
    before phase 2 finished. Returns (needs_detail_ids, brand_new_ids) so the
    caller knows which ids still need a placeholder row created.

    Repost detection piggybacks on this same batch query rather than
    needing its own detail-page re-fetch: a job only reappears in a
    TIME_RANGE_DAY search if it was posted or reposted within that window,
    so an already-known id showing up here again after a >REPOST_GAP_DAYS
    gap is itself the signal. Only counted for jobs still open — `applied`
    or `not_interested` means the case is closed and shouldn't keep
    collecting signal. `expired` gets cleared on a detected repost, since
    that flag means "looked dead last time I checked" and a repost is
    direct evidence that's no longer true."""
    rows = session.query(Job).filter(Job.job_id.in_(job_ids)).all()
    existing = {job.job_id: job for job in rows}

    now = utcnow()
    now_naive = now.replace(tzinfo=None)  # SQLite has no real timezone-aware datetime type, so a last_seen_at read back from the DB always comes back naive even though utcnow() (used to write it) is tz-aware — compare naive-to-naive
    fully_known_ids = []
    reposted = []
    for jid, job in existing.items():
        if not job.detail_fetched:
            continue
        fully_known_ids.append(jid)
        if (
            not job.applied
            and not job.not_interested
            and now_naive - job.last_seen_at > timedelta(days=REPOST_GAP_DAYS)
        ):
            job.repost_count += 1
            job.expired = False
            reposted.append((jid, job.title))
        job.last_seen_at = now
    session.commit()

    needs_detail_ids = [jid for jid in job_ids if jid not in existing or not existing[jid].detail_fetched]
    brand_new_ids = {jid for jid in needs_detail_ids if jid not in existing}
    resumed = len(needs_detail_ids) - len(brand_new_ids)

    print(f"  {len(fully_known_ids)} already known, {len(needs_detail_ids)} need detail"
          + (f" ({resumed} resumed placeholders)" if resumed else ""))
    if reposted:
        print(f"  {len(reposted)} known job(s) reposted (reappeared after {REPOST_GAP_DAYS}+ days) "
              f"— repost_count bumped, expired cleared:")
        for jid, title in reposted:
            print(f"    {jid}: {title!r}")
    return needs_detail_ids, brand_new_ids


def filter_relevant_ids(
    session, track: str, cards_by_id: dict, needs_detail_ids: list[str], brand_new_ids: set[str]
) -> list[str]:
    """Drops any id whose title doesn't match `track`'s curated terms
    (analysis/title_filter.py), whose title reads as Director/Staff/Principal-
    level or above (is_too_senior_title — a seniority mismatch regardless of
    track), or whose company is a known agency/staffing posting
    (judge/agency_blocklist.py, matched by company name off the same
    list-page card — see parse_job_card), before it gets a placeholder row
    or a full detail fetch. LinkedIn's keyword search matches a posting's
    whole text, not just its title, so plenty of off-track noise (e.g.
    "Accounting Paid Consultant" for a "machine learning" search) shows up in
    the raw id list; agency/gig platforms (Turing, Braintrust, micro1, ...)
    show up as genuinely on-track titles but were previously only caught
    after a full detail fetch, by delete_agency_jobs() at the end of the
    run. A resumed placeholder (already in the DB from an earlier
    interrupted run) that turns out irrelevant gets deleted outright rather
    than left to be retried forever — brand-new ids that fail a filter
    were never saved in the first place, so there's nothing to clean up."""
    relevant_ids = []
    dropped_titles = []
    dropped_senior = []
    dropped_agencies = []
    for job_id in needs_detail_ids:
        card = cards_by_id.get(job_id, {})
        title = card.get("title")
        company = card.get("company")
        # a still-None title/company here already got its own WARNING from
        # linkedin_scraper.retry_missing_titles() — no need to repeat it
        if not is_relevant_title(title, track):
            dropped_titles.append((job_id, title))
            if job_id not in brand_new_ids:
                session.query(Job).filter(Job.job_id == job_id).delete(synchronize_session=False)
        elif is_too_senior_title(title):
            dropped_senior.append((job_id, title))
            if job_id not in brand_new_ids:
                session.query(Job).filter(Job.job_id == job_id).delete(synchronize_session=False)
        elif is_agency_company_name(session, company):
            dropped_agencies.append((job_id, company))
            if job_id not in brand_new_ids:
                session.query(Job).filter(Job.job_id == job_id).delete(synchronize_session=False)
        else:
            relevant_ids.append(job_id)

    if dropped_titles:
        print(f"  {len(dropped_titles)} dropped as off-track (title didn't match {track!r} terms):")
        for job_id, title in dropped_titles:
            print(f"    {job_id}: {title!r}")
    if dropped_senior:
        print(f"  {len(dropped_senior)} dropped as too senior (Director/Staff/Principal-level title):")
        for job_id, title in dropped_senior:
            print(f"    {job_id}: {title!r}")
    if dropped_agencies:
        print(f"  {len(dropped_agencies)} dropped as agency/staffing postings (company blocklist):")
        for job_id, company in dropped_agencies:
            print(f"    {job_id}: {company!r}")
    session.commit()
    return relevant_ids


def process_keyword(
    driver,
    session,
    keyword: str,
    time_range: str,
    max_pages: int,
    screening_queue: queue.Queue,
    resumes_by_track: dict[str, str],
    default_content: str | None,
    geo_id: str | None = None,
    work_type: str = WORK_TYPE_REMOTE,
    run_label: str | None = None,
    broad_match: bool = False,
) -> None:
    """Pages through results one page at a time, running each page's dedup
    check + full detail fetch immediately after that page loads, rather than
    collecting every page's job_ids first and only then starting detail
    work. Used for both the initial backfill (TIME_RANGE_MONTH,
    MAX_PAGES_BACKFILL) and the routine daily run (TIME_RANGE_DAY,
    MAX_PAGES_DAILY) — a prior two-phase split for the daily run (collect
    every keyword's ids first, fetch detail second) was meant to minimize
    wall-clock time per keyword and so limit how much LinkedIn's live feed
    could drift mid-pagination, but proved unreliable in practice; this
    single interleaved pass is simpler and matches the backfill path that
    already works.

    broad_match just threads through to get_job_cards_on_page — see that
    function's docstring for what it changes (a different LinkedIn search
    endpoint with related-term matching instead of literal keyword text).
    """
    track = track_for_keyword(keyword)
    run_label = run_label or keyword
    print(f"\n=== Processing keyword: {keyword!r} (track={track}, run_label={run_label!r}) ===")

    num_found = 0
    num_new = 0
    start = 0
    page_num = 1
    consecutive_empty_pages = 0
    consecutive_page_timeouts = 0
    PAGE_TIMEOUT_LIMIT = 3  # a couple of one-off hangs (LinkedIn slow to load a single page) shouldn't cost the rest of the keyword; this many *in a row* is a real signal something's wrong with the whole run, not just one page

    while True:
        print(f"\n--- Page {page_num} (start={start}) ---")
        try:
            cards = get_job_cards_on_page(driver, keyword, start, time_range, geo_id=geo_id, work_type=work_type, broad_match=broad_match)  # LinkedInBlockedError propagates up uncaught — see main()
        except TimeoutException:
            # get_job_cards_on_page already retried once internally (safe_get)
            # — treat this the same as one job's detail page timing out: skip
            # this page, move to the next one, rather than giving up on the
            # whole keyword over a single hang. Only stop the keyword if
            # PAGE_TIMEOUT_LIMIT pages in a row fail this way — that's the
            # actual signal of a systemic problem (LinkedIn throttling this
            # run hard), not one slow page.
            consecutive_page_timeouts += 1
            print(f"  Page {page_num} load timed out twice — skipping this page "
                  f"({consecutive_page_timeouts}/{PAGE_TIMEOUT_LIMIT} consecutive).")
            if consecutive_page_timeouts >= PAGE_TIMEOUT_LIMIT:
                print(f"  {consecutive_page_timeouts} consecutive page timeouts — stopping this keyword early.")
                break
            start += 25
            page_num += 1
            jitter(3.0, 6.0)
            continue
        consecutive_page_timeouts = 0
        if not cards:
            print(f"  Page {page_num} returned no job ids — end of results for this keyword.")
            break

        job_ids = [card["job_id"] for card in cards]
        cards_by_id = {card["job_id"]: card for card in cards}
        print(f"  Page {page_num}: {len(job_ids)} job ids")
        num_found += len(job_ids)
        needs_detail_ids, brand_new_ids = dedup_page(session, job_ids)
        relevant_ids = filter_relevant_ids(session, track, cards_by_id, needs_detail_ids, brand_new_ids)

        if broad_match:
            # Skip the expensive full detail-page fetch entirely for cards
            # the LIST view already confidently tags Hybrid/On-site — see
            # scraper/linkedin_scraper.py's _extract_card_workplace_hint
            # docstring for why LinkedIn's own "Remote" filter click can't
            # be trusted alone. Only excludes a CONFIDENT non-remote hint;
            # None (unparseable) or "Remote" both proceed to the fetch, so
            # this can't lose a genuinely-remote job over a card-text miss
            # — the detail-fetch-level check below is the safety net for
            # cases where the list tag and the job's own detail page
            # disagree.
            confident_non_remote = {
                jid for jid in relevant_ids
                if cards_by_id[jid].get("workplace_hint") in ("Hybrid", "On-site")
            }
            if confident_non_remote:
                print(f"  {len(confident_non_remote)} card(s) tagged Hybrid/On-site in the list view — skipping detail fetch:")
                for jid in confident_non_remote:
                    print(f"    {jid}: {cards_by_id[jid]['title']!r} ({cards_by_id[jid]['workplace_hint']})")
            relevant_ids = [jid for jid in relevant_ids if jid not in confident_non_remote]

        for job_id in relevant_ids:
            print(f"  New job {job_id} ({cards_by_id[job_id]['title']!r}) — fetching full detail...")
            try:
                detail = scrape_job_detail(driver, keyword, job_id, time_range, geo_id=geo_id, work_type=work_type, broad_match=broad_match)
            except TimeoutException:
                # Already retried once internally (safe_get) — one job's page
                # hanging twice shouldn't cost the rest of the run; it has no
                # Job row yet (create_placeholder_job isn't called on this
                # path), so it's simply retried fresh on the next scrape.
                print(f"  Job {job_id} detail page timed out twice — skipping, will retry next run.")
                continue
            if broad_match and detail["workplace_type"] != "Remote":
                # LinkedIn's own "Remote" filter click on the broad-match
                # endpoint (see click_remote_filter) doesn't reliably
                # restrict results to remote-only jobs — confirmed live
                # 2026-08-17: On-site/Hybrid postings kept slipping through
                # despite the filter chip being active with no error. Our
                # own workplace_type extraction (from the job's own detail
                # page) is accurate and independent of that filter, so it's
                # the more trustworthy signal here — post-filter on it
                # rather than trusting LinkedIn's list-level filter. No Job
                # row is created for a skipped job (same as a detail-page
                # timeout, see above) — it'll just get re-checked and
                # re-skipped if it resurfaces on a future run, a small,
                # bounded cost rather than one worth tracking state for.
                print(f"  Job {job_id} ({detail['title']!r}) is {detail['workplace_type']!r}, not Remote — skipping (broad-match's Remote filter isn't reliable, see CONTEXT.md).")
                continue
            job = save_new_job(session, keyword, track, job_id, detail)
            num_new += 1

            resume_content = resume_for_screening(job, resumes_by_track, default_content)
            if resume_content is not None:
                screening_queue.put((job.id, resume_content))

            if num_new % 5 == 0:
                print(f"  >>> Progress: {num_new} new jobs saved so far for {keyword!r}")

        if needs_detail_ids:
            consecutive_empty_pages = 0
        else:
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= CONSECUTIVE_EMPTY_PAGES_LIMIT:
                print(f"  {consecutive_empty_pages} consecutive pages with nothing new — "
                      f"stopping (LinkedIn keeps serving pages past the real result set).")
                break

        if page_num >= max_pages:
            print(f"  Hit max_pages={max_pages} safety cap — stopping.")
            break

        start += 25          # LinkedIn's confirmed per-page increment
        page_num += 1
        jitter(3.0, 6.0)      # longer pause between page loads than the brief pause within a page
        maybe_take_a_break()  # occasional longer pause, on top of the routine jitter above — see linkedin_scraper.py

    session.add(ScrapeRun(keyword=run_label, track=track, num_found=num_found, num_new=num_new, status="success"))
    session.commit()
    print(f"  Done: {num_found} found, {num_new} new")


def main() -> None:
    # No args = routine daily run (last 24h). `--week` = one-off catch-up
    # scrape (last 7 days) — run by hand when the daily schedule missed a
    # stretch of days. `--initial` = one-time backfill (last 30 days) — run
    # by hand once, not on the schedule. All three page through results one
    # page at a time, fetching full detail for new jobs immediately (see
    # process_keyword) — they only differ in time window and page-count
    # ceiling.
    initial = "--initial" in sys.argv
    week = "--week" in sys.argv
    mode = "initial" if initial else "week" if week else "daily"
    setup_file_logging(mode)

    # --only=<keyword> restricts this run to one configured keyword (e.g.
    # a one-off "just re-check llm over the past week" without also paying
    # for a full week-window re-scrape of every other keyword).
    only_keyword = next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--only=")), None)
    searches = [s for s in SEARCHES if s[0] == only_keyword] if only_keyword else SEARCHES
    if only_keyword and not searches:
        print(f"--only={only_keyword!r} doesn't match any configured keyword ({[s[0] for s in SEARCHES]!r}).")
        sys.exit(1)

    if not PROFILE_DIR.exists():
        print(f"No Chrome profile found at {PROFILE_DIR}. Run setup_chrome_profile.py first.")
        sys.exit(1)

    init_db()
    session = get_session()

    # Loaded once, up front, and handed down to every process_keyword() call
    # (see judge/eligibility.py) — screening now starts the moment each job
    # is saved rather than waiting for the whole scrape to finish, so this
    # can't be loaded lazily inside run_screening() at the end anymore.
    resumes_by_track, default_content = load_resumes(session)
    if not resumes_by_track and default_content is None:
        raise RuntimeError("No resume in DB — nothing to screen against.")

    # A pool of persistent worker threads, started before scraping begins
    # and fed live via screening_queue as process_keyword() saves new jobs
    # (see _screening_worker's docstring) — screening keeps pace with the
    # scraper instead of only starting after every keyword is done.
    screening_queue: queue.Queue = queue.Queue()
    screening_workers = [
        threading.Thread(target=_screening_worker, args=(screening_queue,), daemon=True)
        for _ in range(SCREENING_WORKERS)
    ]
    for w in screening_workers:
        w.start()

    driver = build_driver()

    time_range = {"initial": TIME_RANGE_MONTH, "week": TIME_RANGE_WEEK, "daily": TIME_RANGE_DAY}[mode]
    max_pages = {"initial": MAX_PAGES_BACKFILL, "week": MAX_PAGES_WEEK, "daily": MAX_PAGES_DAILY}[mode]
    label = {
        "initial": "Initial backfill run (last 30 days)",
        "week": "One-off catch-up run (last 7 days)",
        "daily": "Daily scrape run (last 24h)",
    }[mode]

    try:
        print(f"=== {label} ===")
        for keyword, geo_id, work_type, run_label, broad_match in searches:
            try:
                process_keyword(
                    driver, session, keyword, time_range, max_pages,
                    screening_queue, resumes_by_track, default_content,
                    geo_id=geo_id, work_type=work_type, run_label=run_label,
                    broad_match=broad_match,
                )
            except LinkedInBlockedError as e:
                track = track_for_keyword(keyword)
                session.add(ScrapeRun(
                    keyword=run_label or keyword, track=track, num_found=0, num_new=0,
                    status="error", error_message=f"blocked: {e.reason}",
                ))
                session.commit()
                if e.reason == "logged_out":
                    print("LinkedIn session expired — re-run setup_chrome_profile.py to log in again. Stopping this run.")
                else:
                    print("LinkedIn served a security checkpoint. Stopping this run — do NOT retry immediately.")
                break   # stop processing remaining keywords too — a block affects the whole session, not just one keyword
            except BroadMatchDegradedError as e:
                # Unlike LinkedInBlockedError, this is specific to one
                # keyword's broad-match health (see its docstring) — not
                # necessarily a sign the whole session/account is
                # compromised, so continue to the next keyword rather than
                # stopping the run outright.
                track = track_for_keyword(keyword)
                session.add(ScrapeRun(
                    keyword=run_label or keyword, track=track, num_found=0, num_new=0,
                    status="error", error_message=f"broad_match_degraded: {e.reason}",
                ))
                session.commit()
                print(f"  Broad-match health check failed for {keyword!r} — reason: {e.reason}. Skipping this keyword.")
                continue

        deleted = delete_agency_jobs(session)
        print(f"\n=== Deleted {deleted} agency/staffing postings (blocklist cleanup) ===")
    finally:
        driver.quit()
        session.close()

    print("\n=== Waiting for the screening queue to drain ===")
    screening_queue.join()               # blocks until every enqueued job has been screened
    for _ in screening_workers:
        screening_queue.put(None)        # one sentinel per worker — see _screening_worker
    for w in screening_workers:
        w.join()

    # Catches anything the live queue didn't cover — e.g. jobs left over
    # from a run that was killed before its queue drained, or a DB that
    # predates this live-screening path entirely. run_screening() re-queries
    # for every job in the DB without a ScreeningResult yet, not just ones
    # from this run, so it's a safe no-op sweep when the queue already got
    # everything.
    print("\n=== Catching up any jobs the live queue missed ===")
    run_screening()


if __name__ == "__main__":
    main()
