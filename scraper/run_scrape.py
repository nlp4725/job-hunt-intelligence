"""
Real orchestrator: scrape all 4 keywords, dedup against the DB, only fetch
full detail for genuinely new jobs, extract skills, and persist everything.

This is the script launchd will eventually call on a schedule. Run directly
for now:
    python scraper/run_scrape.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # so `db.` / `analysis.` imports resolve when run directly

from analysis.skills_extractor import extract_skills
from analysis.title_filter import is_relevant_title
from db.models import Company, Job, JobSkill, ScrapeRun, track_for_keyword, utcnow
from db.session import get_session, init_db
from scraper.linkedin_scraper import (
    LinkedInBlockedError,
    PROFILE_DIR,
    TIME_RANGE_DAY,
    TIME_RANGE_MONTH,
    build_driver,
    get_job_cards_on_page,
    jitter,
    scrape_job_detail,
)

KEYWORDS = ["machine learning", "ai engineer", "ai scientist", "product manager"]

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
MAX_PAGES_BACKFILL = 200   # TIME_RANGE_MONTH pool is much larger, but still needs a hard ceiling


def get_or_create_company(session, name: str | None, industry: str | None, size: str | None) -> Company | None:
    if not name:
        return None
    company = session.query(Company).filter(Company.name == name).first()
    if company:
        # backfill industry/size if we now have info we didn't have before
        # (e.g. an earlier scrape found this company with a listing that
        # didn't render its About-card properly)
        if industry and not company.industry:
            company.industry = industry
        if size and not company.size:
            company.size = size
        return company

    company = Company(name=name, industry=industry, size=size, analyzed_at=utcnow() if industry else None)
    session.add(company)
    session.flush()  # assigns company.id without needing a full commit yet
    return company


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


def save_new_job(session, keyword: str, track: str, job_id: str, detail: dict) -> None:
    """Insert or complete a job row with full scraped detail, and commit
    immediately — if a block hits mid-run, everything saved so far survives.
    Upserts rather than always inserting because a placeholder row (id only,
    detail_fetched=False) may already exist here — created either earlier
    this run's phase 1, or by a previous run that got interrupted before
    finishing phase 2.

    Title-relevance filtering (analysis/title_filter.py) already happened
    upstream, at list-page collection time (see filter_relevant_ids) — by
    the time a job_id reaches this function its title has already passed
    the track's curated term list, so is_relevant just keeps its schema
    default (True) here.
    """
    company = get_or_create_company(session, detail["company"], detail["industry"], detail["company_size"])

    job = session.query(Job).filter(Job.job_id == job_id).first()
    if job is None:
        job = Job(job_id=job_id, url=detail["url"], keyword_matched=keyword, track=track)
        session.add(job)

    job.url = detail["url"]
    job.title = detail["title"]
    job.company_name = detail["company"]
    job.company_id = company.id if company else None
    job.location = detail["location"]
    job.keyword_matched = keyword
    job.track = track
    job.raw_text = detail["raw_text"]
    job.salary_text = detail["salary_text"]
    job.posted_date = detail["posted_date"]
    job.applicant_stats = detail["applicant_stats"]
    job.detail_fetched = True

    session.flush()          # assigns job.id (if newly inserted) so JobSkill rows below can reference it

    if detail["raw_text"]:
        for skill_name in extract_skills(detail["raw_text"]):
            session.add(JobSkill(job_id=job.id, skill_name=skill_name))

    session.commit()


def dedup_page(session, job_ids: list[str]) -> tuple[list[str], set[str]]:
    """One batch query for the whole page instead of 25 individual ones.
    Splits job_ids into "already have full detail" (bump last_seen_at,
    skip) vs "still needs detail" — either a genuinely new id (no row at
    all yet) or a placeholder row left by a run that was interrupted before
    phase 2 finished. Returns (needs_detail_ids, brand_new_ids) so the
    caller knows which ids still need a placeholder row created."""
    rows = session.query(Job.job_id, Job.detail_fetched).filter(Job.job_id.in_(job_ids)).all()
    existing = dict(rows)

    fully_known_ids = [jid for jid, fetched in existing.items() if fetched]
    session.query(Job).filter(Job.job_id.in_(fully_known_ids)).update(
        {Job.last_seen_at: utcnow()}, synchronize_session=False
    )
    session.commit()

    needs_detail_ids = [jid for jid in job_ids if jid not in existing or not existing[jid]]
    brand_new_ids = {jid for jid in needs_detail_ids if jid not in existing}
    resumed = len(needs_detail_ids) - len(brand_new_ids)

    print(f"  {len(fully_known_ids)} already known, {len(needs_detail_ids)} need detail"
          + (f" ({resumed} resumed placeholders)" if resumed else ""))
    return needs_detail_ids, brand_new_ids


def filter_relevant_ids(
    session, track: str, cards_by_id: dict, needs_detail_ids: list[str], brand_new_ids: set[str]
) -> list[str]:
    """Drops any id whose title doesn't match `track`'s curated terms
    (analysis/title_filter.py) before it gets a placeholder row or a full
    detail fetch — LinkedIn's keyword search matches a posting's whole text,
    not just its title, so plenty of off-track noise (e.g. "Accounting Paid
    Consultant" for a "machine learning" search) shows up in the raw id
    list. A resumed placeholder (already in the DB from an earlier
    interrupted run) that turns out irrelevant gets deleted outright rather
    than left to be retried forever — brand-new ids that fail the filter
    were never saved in the first place, so there's nothing to clean up."""
    relevant_ids = []
    dropped_titles = []
    for job_id in needs_detail_ids:
        title = cards_by_id.get(job_id, {}).get("title")
        # a still-None title here already got its own WARNING from
        # linkedin_scraper.retry_missing_titles() — no need to repeat it
        if is_relevant_title(title, track):
            relevant_ids.append(job_id)
        else:
            dropped_titles.append((job_id, title))
            if job_id not in brand_new_ids:
                session.query(Job).filter(Job.job_id == job_id).delete(synchronize_session=False)

    if dropped_titles:
        print(f"  {len(dropped_titles)} dropped as off-track (title didn't match {track!r} terms):")
        for job_id, title in dropped_titles:
            print(f"    {job_id}: {title!r}")
    session.commit()
    return relevant_ids


def process_keyword_backfill(driver, session, keyword: str, time_range: str) -> None:
    """One-time initial-backfill path: pages through results, processing each
    page's dedup check + full detail fetch immediately after that page
    loads, rather than collecting every page's job_ids first and only then
    starting detail work. That's fine for a single backfill run (progress
    shows up page by page instead of a long silent stretch), and pairs with
    TIME_RANGE_MONTH — the pool is large enough that a fully two-phase
    collect-then-fetch split isn't worth the extra bookkeeping here.
    """
    track = track_for_keyword(keyword)
    print(f"\n=== Processing keyword: {keyword!r} (track={track}) ===")

    num_found = 0
    num_new = 0
    start = 0
    page_num = 1
    consecutive_empty_pages = 0

    while True:
        print(f"\n--- Page {page_num} (start={start}) ---")
        cards = get_job_cards_on_page(driver, keyword, start, time_range)     # LinkedInBlockedError propagates up uncaught — see main()
        if not cards:
            print(f"  Page {page_num} returned no job ids — end of results for this keyword.")
            break

        job_ids = [card["job_id"] for card in cards]
        cards_by_id = {card["job_id"]: card for card in cards}
        print(f"  Page {page_num}: {len(job_ids)} job ids")
        num_found += len(job_ids)
        needs_detail_ids, brand_new_ids = dedup_page(session, job_ids)
        relevant_ids = filter_relevant_ids(session, track, cards_by_id, needs_detail_ids, brand_new_ids)

        for job_id in relevant_ids:
            print(f"  New job {job_id} ({cards_by_id[job_id]['title']!r}) — fetching full detail...")
            detail = scrape_job_detail(driver, keyword, job_id, time_range)
            save_new_job(session, keyword, track, job_id, detail)
            num_new += 1

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

        if page_num >= MAX_PAGES_BACKFILL:
            print(f"  Hit MAX_PAGES_BACKFILL={MAX_PAGES_BACKFILL} safety cap — stopping.")
            break

        start += 25          # LinkedIn's confirmed per-page increment
        page_num += 1
        jitter(3.0, 6.0)      # longer pause between page loads than the brief pause within a page

    session.add(ScrapeRun(keyword=keyword, track=track, num_found=num_found, num_new=num_new, status="success"))
    session.commit()
    print(f"  Done: {num_found} found, {num_new} new")


def collect_new_ids(driver, session, keyword: str, time_range: str, seen_this_run: set[str]) -> tuple[list[str], int]:
    """Phase 1 of the daily run: page through list results only (id
    discovery + DB dedup) with no detail fetch — no 3-15s simulated reading
    per job slowing this loop down. Keeping this pass fast in wall-clock
    time is what actually curbs LinkedIn's live feed shifting mid-scan and
    pushing already-seen jobs into the next page's offset window; combined
    with TIME_RANGE_DAY keeping the total pool small to begin with.

    Every genuinely new id gets a placeholder row saved immediately (see
    create_placeholder_job) — so it's recognized if it reappears on a later
    page this run, and survives the process being killed before phase 2.

    `seen_this_run` is shared across all 4 keywords' calls this run: a job
    matching two keywords (e.g. "machine learning" and "ai engineer") would
    otherwise get queued for phase 2 twice, once per keyword, since neither
    placeholder is complete yet when the second keyword's pass runs.
    """
    track = track_for_keyword(keyword)
    new_ids: list[str] = []
    num_found = 0
    start = 0
    page_num = 1
    consecutive_empty_pages = 0

    while True:
        print(f"\n--- [{keyword}] Page {page_num} (start={start}) ---")
        cards = get_job_cards_on_page(driver, keyword, start, time_range)
        if not cards:
            print(f"  Page {page_num} returned no job ids — end of results for this keyword.")
            break

        job_ids = [card["job_id"] for card in cards]
        cards_by_id = {card["job_id"]: card for card in cards}
        print(f"  Page {page_num}: {len(job_ids)} job ids")
        num_found += len(job_ids)
        needs_detail_ids, brand_new_ids = dedup_page(session, job_ids)
        relevant_ids = filter_relevant_ids(session, track, cards_by_id, needs_detail_ids, brand_new_ids)

        for job_id in relevant_ids:
            if job_id in brand_new_ids:
                create_placeholder_job(session, keyword, track, job_id)

        fresh_for_this_keyword = [jid for jid in relevant_ids if jid not in seen_this_run]
        seen_this_run.update(fresh_for_this_keyword)
        new_ids.extend(fresh_for_this_keyword)

        if needs_detail_ids:
            consecutive_empty_pages = 0
        else:
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= CONSECUTIVE_EMPTY_PAGES_LIMIT:
                print(f"  {consecutive_empty_pages} consecutive pages with nothing new — "
                      f"stopping (LinkedIn keeps serving pages past the real result set).")
                break

        if page_num >= MAX_PAGES_DAILY:
            print(f"  Hit MAX_PAGES_DAILY={MAX_PAGES_DAILY} safety cap — stopping.")
            break

        start += 25
        page_num += 1
        jitter(3.0, 6.0)

    return new_ids, num_found


def run_daily(driver, session) -> None:
    """Routine daily run: collect every keyword's new job ids first (fast —
    list pages only, TIME_RANGE_DAY), then fetch full detail for all of them
    one by one (slow — simulated reading per job). Splitting these into two
    passes is what beats the drift/inconsistency the interleaved backfill
    approach suffers from: the whole id-collection pass across all 4
    keywords finishes in minutes rather than the tens of minutes it'd take
    interleaved with detail fetches, leaving much less time for LinkedIn's
    live feed to shift underneath the pagination.
    """
    run_started_at = utcnow()
    print(f"\n=== Daily scrape run started at {run_started_at.isoformat()} ===")

    new_ids_by_keyword: dict[str, list[str]] = {}
    num_found_by_keyword: dict[str, int] = {kw: 0 for kw in KEYWORDS}
    num_new_by_keyword: dict[str, int] = {kw: 0 for kw in KEYWORDS}
    status = "success"
    error_message = None

    seen_this_run: set[str] = set()   # shared across keywords so a job matching 2+ keywords is only queued for phase 2 once

    try:
        print("\n--- Phase 1: collecting new ids for all keywords ---")
        for keyword in KEYWORDS:
            new_ids, num_found = collect_new_ids(driver, session, keyword, TIME_RANGE_DAY, seen_this_run)
            new_ids_by_keyword[keyword] = new_ids
            num_found_by_keyword[keyword] = num_found
            print(f"  {keyword!r}: {num_found} found, {len(new_ids)} new")

        total_new = sum(len(ids) for ids in new_ids_by_keyword.values())
        print(f"\n=== Phase 1 done: {total_new} new jobs to fetch across {len(KEYWORDS)} keywords ===")

        print("\n--- Phase 2: fetching full detail, one job at a time ---")
        processed = 0
        for keyword in KEYWORDS:
            track = track_for_keyword(keyword)
            for job_id in new_ids_by_keyword[keyword]:
                print(f"  New job {job_id} ({keyword}) — fetching full detail...")
                detail = scrape_job_detail(driver, keyword, job_id, TIME_RANGE_DAY)
                save_new_job(session, keyword, track, job_id, detail)
                num_new_by_keyword[keyword] += 1
                processed += 1
                if processed % 5 == 0:
                    print(f"  >>> Progress: {processed}/{total_new} new jobs saved so far")

    except LinkedInBlockedError as e:
        status = "error"
        error_message = f"blocked: {e.reason}"
        if e.reason == "logged_out":
            print("LinkedIn session expired — re-run setup_chrome_profile.py to log in again. Stopping this run.")
        else:
            print("LinkedIn served a security checkpoint. Stopping this run — do NOT retry immediately.")

    finished_at = utcnow()
    for keyword in KEYWORDS:
        session.add(ScrapeRun(
            keyword=keyword, track=track_for_keyword(keyword),
            num_found=num_found_by_keyword[keyword], num_new=num_new_by_keyword[keyword],
            status=status, error_message=error_message,
        ))
    session.commit()

    total_new_saved = sum(num_new_by_keyword.values())
    print(f"\n=== Daily scrape run finished at {finished_at.isoformat()} "
          f"(started {run_started_at.isoformat()}) — {total_new_saved} new jobs saved ===")
    for keyword in KEYWORDS:
        print(f"  {keyword!r}: {num_found_by_keyword[keyword]} found, {num_new_by_keyword[keyword]} new")


def main() -> None:
    # No args = routine daily run (last 24h, two-phase) — what launchd calls
    # on schedule. `--initial` = one-time backfill (last 30 days, single-phase
    # interleaved) — run by hand once, not on the schedule.
    initial = "--initial" in sys.argv
    setup_file_logging("initial" if initial else "daily")

    if not PROFILE_DIR.exists():
        print(f"No Chrome profile found at {PROFILE_DIR}. Run setup_chrome_profile.py first.")
        sys.exit(1)

    init_db()
    session = get_session()
    driver = build_driver()

    try:
        if initial:
            print("=== Initial backfill run (last 30 days) ===")
            for keyword in KEYWORDS:
                try:
                    process_keyword_backfill(driver, session, keyword, TIME_RANGE_MONTH)
                except LinkedInBlockedError as e:
                    track = track_for_keyword(keyword)
                    session.add(ScrapeRun(
                        keyword=keyword, track=track, num_found=0, num_new=0,
                        status="error", error_message=f"blocked: {e.reason}",
                    ))
                    session.commit()
                    if e.reason == "logged_out":
                        print("LinkedIn session expired — re-run setup_chrome_profile.py to log in again. Stopping this run.")
                    else:
                        print("LinkedIn served a security checkpoint. Stopping this run — do NOT retry immediately.")
                    break   # stop processing remaining keywords too — a block affects the whole session, not just one keyword
        else:
            run_daily(driver, session)
    finally:
        driver.quit()
        session.close()


if __name__ == "__main__":
    main()
