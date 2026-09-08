"""
Selenium-free job upsert logic — shared by scraper/run_scrape.py (bulk
Selenium scraping) and backend/app.py (the browser-extension capture
endpoint), so a Flask process can import job-writing logic without pulling
in undetected_chromedriver via scraper.linkedin_scraper.

Extracted verbatim from scraper/run_scrape.py — see that file's git history
for prior context/docstrings on the two functions below.
"""

from analysis.duplicate_detector import find_duplicate_job
from analysis.salary_parser import parse_salary_range
from analysis.workplace_from_raw_text import WRITEBACK_ALLOWED, infer_workplace_type
from analysis.skills_extractor import extract_skills
from db.models import Company, Job, JobSkill, utcnow


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


def save_new_job(session, keyword: str, track: str, job_id: str, detail: dict) -> Job:
    """Insert or complete a job row with full scraped detail, and commit
    immediately — if a block hits mid-run, everything saved so far survives.
    Upserts rather than always inserting because a placeholder row (id only,
    detail_fetched=False) may already exist here — created either earlier
    this run's phase 1, or by a previous run that got interrupted before
    finishing phase 2.

    `detail` dict shape is a hand-maintained contract with the browser
    extension's extension/content/extract.js:extractJobDetail() — keep the
    two in sync.
    """
    company = get_or_create_company(session, detail["company"], detail["industry"], detail["company_size"])

    # One timestamp for the whole capture, so posted_date_seen_at and
    # last_seen_at agree exactly. last_seen_at is now set explicitly here —
    # Job.last_seen_at deliberately no longer carries onupdate=utcnow.
    now = utcnow()

    job = session.query(Job).filter(Job.job_id == job_id).first()
    if job is None:
        # first_seen_at must be stamped from the SAME `now` as last_seen_at.
        # Left to its column default it is evaluated at flush time, a few
        # milliseconds AFTER `now` was taken, so a brand-new row landed with
        # last_seen_at < first_seen_at — which is impossible by definition and
        # trips the temporal-sanity check in tests_and_eval/ingest_check.py.
        # (Caught on the gate's first real run, 3 rows, 2026-09-08.)
        job = Job(job_id=job_id, url=detail["url"], keyword_matched=keyword, track=track,
                  first_seen_at=now)
        session.add(job)

    job.url = detail["url"]
    job.title = detail["title"]
    job.company_name = detail["company"]
    job.company_id = company.id if company else None
    job.location = detail["location"]
    # LinkedIn's own tag when the extension could read it. When it couldn't —
    # which is what every LinkedIn layout rebuild looks like from here (the
    # September 2026 one left workplace_type NULL on 97% of captures for
    # weeks) — fall back to inferring it from the JD text rather than storing
    # nothing. That downgrade costs precision, which workplace_type_source
    # records honestly, instead of costing the field entirely. Only the tiers
    # that cleared the precision bar in analysis/workplace_from_raw_text.py's
    # eval are ever written.
    if detail["workplace_type"]:
        job.workplace_type = detail["workplace_type"]
        job.workplace_type_source = "linkedin"
    elif not job.workplace_type and detail["raw_text"]:
        predicted, confidence = infer_workplace_type(detail["raw_text"], detail["title"])
        if predicted and (confidence, predicted) in WRITEBACK_ALLOWED:
            job.workplace_type = predicted
            job.workplace_type_source = "raw_text"
    job.keyword_matched = keyword
    job.track = track
    job.raw_text = detail["raw_text"]
    job.salary_text = detail["salary_text"]
    job.salary_min, job.salary_max = parse_salary_range(detail["salary_text"])
    # Safety net: a still-missing posted_date must never make an
    # already-screened job invisible on the dashboard (backend/templates/
    # index.html unconditionally excludes any job with no posted_at). If
    # this capture found a real value, use it; if not, keep whatever's
    # already on the row rather than blanking out a previously-good value
    # (the extension can re-capture an already-saved job on a revisit); only
    # fall back to "today" (the actual collection date, via last_seen_at)
    # when there's truly nothing to fall back to.
    if detail["posted_date"]:
        job.posted_date = detail["posted_date"]
        job.posted_date_seen_at = now
    elif not job.posted_date:
        job.posted_date = "0 hours ago"
        job.posted_date_seen_at = now
    job.applicant_stats = detail["applicant_stats"]
    # Only True when the fetch actually got real content. Found 2026-08-14:
    # scrape_job_detail() never validates its own extraction — if the page
    # hadn't finished rendering (a real risk during that day's apparent
    # post-page-7 LinkedIn throttling, see linkedin_scraper.py's
    # get_job_cards_on_page docstring), it silently returns a dict of
    # mostly-None fields, and this used to mark detail_fetched=True
    # unconditionally anyway — permanently hiding the failure, since
    # dedup_page's retry logic only requeues rows where detail_fetched is
    # still False. 61 jobs from that one run ended up stuck this way (title
    # AND raw_text both empty); one older row had raw_text but no title.
    # Require both before considering the fetch complete.
    job.detail_fetched = bool(detail["title"]) and bool(detail["raw_text"])
    # We just read this listing off LinkedIn, so this is a genuine sighting.
    # Explicit because Job.last_seen_at no longer carries onupdate=utcnow.
    job.last_seen_at = now

    session.flush()          # assigns job.id (if newly inserted) so JobSkill rows below can reference it

    if detail["raw_text"]:
        # Idempotent: save_new_job can be called more than once for the same
        # job.id — the extension's dwell timer can re-capture an already-
        # captured job on a revisit — so re-adding a skill already recorded
        # here would trip JobSkill's (job_id, skill_name) unique constraint.
        existing_skills = {
            row[0] for row in session.query(JobSkill.skill_name).filter(JobSkill.job_id == job.id).all()
        }
        for skill_name in extract_skills(detail["raw_text"]):
            if skill_name not in existing_skills:
                session.add(JobSkill(job_id=job.id, skill_name=skill_name))

    job.duplicate_of_job_id = find_duplicate_job(session, job.company_name, job.raw_text, exclude_job_id=job.id)

    session.commit()
    return job
