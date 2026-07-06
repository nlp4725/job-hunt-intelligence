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
from db.models import Company, Job, JobSkill, ScrapeRun, track_for_keyword, utcnow
from db.session import get_session, init_db
from scraper.linkedin_scraper import (
    LinkedInBlockedError,
    PROFILE_DIR,
    build_driver,
    get_job_ids_on_page,
    jitter,
    scrape_job_detail,
)

KEYWORDS = ["machine learning", "ai engineer", "ai scientist", "product manager"]


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


def process_keyword(driver, session, keyword: str) -> None:
    """Pages through results, but — unlike scrape_keyword() — processes each
    page's dedup check + full detail fetch immediately after that page
    loads, rather than collecting every page's job_ids first and only then
    starting detail work. That earlier design meant a long silent stretch of
    pagination (no scrolling, no per-job output) before anything else
    happened, which for a keyword with many pages looked like nothing was
    working. This way, progress (and scrolling) shows up page by page.
    """
    track = track_for_keyword(keyword)
    print(f"\n=== Processing keyword: {keyword!r} (track={track}) ===")

    num_found = 0
    num_new = 0
    start = 0
    page_num = 1

    # Initial-run policy: page until LinkedIn is exhausted. Routine daily-run
    # pacing is still an open decision — revisit once the backfill has run
    # once and we know real per-keyword volume.
    while True:
        print(f"\n--- Page {page_num} (start={start}) ---")
        job_ids = get_job_ids_on_page(driver, keyword, start)     # LinkedInBlockedError propagates up uncaught — see main()
        if not job_ids:
            print(f"  Page {page_num} returned no job ids — end of results for this keyword.")
            break

        print(f"  Page {page_num}: {len(job_ids)} job ids")
        num_found += len(job_ids)

        # One batch query for the whole page instead of 25 individual ones:
        # find which of this page's job_ids are already in the DB, and bump
        # their last_seen_at. Whatever's left (the "remainder") is new.
        already_known = {
            row[0] for row in session.query(Job.job_id).filter(Job.job_id.in_(job_ids)).all()
        }
        session.query(Job).filter(Job.job_id.in_(already_known)).update(
            {Job.last_seen_at: utcnow()}, synchronize_session=False
        )
        new_ids = [jid for jid in job_ids if jid not in already_known]
        print(f"  {len(already_known)} already known, {len(new_ids)} new")

        for job_id in new_ids:
            print(f"  New job {job_id} — fetching full detail...")
            detail = scrape_job_detail(driver, keyword, job_id)

            company = get_or_create_company(session, detail["company"], detail["industry"], detail["company_size"])

            job = Job(
                job_id=job_id,
                url=detail["url"],
                title=detail["title"],
                company_name=detail["company"],
                company_id=company.id if company else None,
                location=detail["location"],
                keyword_matched=keyword,
                track=track,
                raw_text=detail["raw_text"],
                salary_text=detail["salary_text"],
                posted_date=detail["posted_date"],
                applicant_stats=detail["applicant_stats"],
            )
            session.add(job)
            session.flush()          # assigns job.id so JobSkill rows below can reference it

            if detail["raw_text"]:
                for skill_name in extract_skills(detail["raw_text"]):
                    session.add(JobSkill(job_id=job.id, skill_name=skill_name))

            num_new += 1
            session.commit()          # commit after each new job — if we get blocked mid-run, everything so far survives

            if num_new % 5 == 0:
                print(f"  >>> Progress: {num_new} new jobs saved so far for {keyword!r}")

        start += 25          # LinkedIn's confirmed per-page increment
        page_num += 1
        jitter(3.0, 6.0)      # longer pause between page loads than the brief pause within a page

    session.add(ScrapeRun(keyword=keyword, track=track, num_found=num_found, num_new=num_new, status="success"))
    session.commit()
    print(f"  Done: {num_found} found, {num_new} new")


def main() -> None:
    if not PROFILE_DIR.exists():
        print(f"No Chrome profile found at {PROFILE_DIR}. Run setup_chrome_profile.py first.")
        sys.exit(1)

    init_db()
    session = get_session()
    driver = build_driver()

    try:
        for keyword in KEYWORDS:
            try:
                process_keyword(driver, session, keyword)
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
    finally:
        driver.quit()
        session.close()


if __name__ == "__main__":
    main()
