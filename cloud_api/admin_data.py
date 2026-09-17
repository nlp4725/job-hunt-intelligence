"""Admin-route logic on shared tables: captures, cached lookups, the agency
list, collection pages, expiry and member plans (productization plan §2, §6).

Runs as jhi_admin_api, which has no access to per-user tables. A capture never
scores users itself: the route publishes a rescore message after the commit
(cloud_api/rescore.py) and the jhi-rescore Lambda scores it for every user.
"""

from sqlalchemy import text

from analysis.title_filter import classify_track
from db.classify_job_seniority import PROMPT_VERSION
from cloud_api.observability import Timer, log_event, metric
from db.cloud_models import PLANS, JobSeniority
from db.job_writer import save_new_job
from db.models import CollectionPage, ExtractionEvent, Job
from judge.agency_blocklist import AGENCY_COMPANY_NAME_SUBSTRINGS, is_agency_job
from judge.seniority_fit import format_posting

# The extension's extractJobDetail() fields, as in backend/app.py.
DETAIL_FIELDS = ("company", "industry", "company_size", "url", "title", "location", "workplace_type",
                 "raw_text", "salary_text", "posted_date", "applicant_stats")


def job_summary(job: Job) -> dict:
    return {"id": job.id, "job_id": job.job_id, "title": job.title, "company": job.company_name,
            "track": job.track, "expired": job.expired, "duplicate_of": job.duplicate_of_job_id}


def seniority_view(row: JobSeniority | None) -> dict | None:
    return None if row is None else {"level": row.level, "is_contract": row.is_contract}


def _seniority(db, job: Job) -> JobSeniority | None:
    return db.query(JobSeniority).filter(JobSeniority.job_id == job.id).one_or_none()


def _record_extraction_event(db, linkedin_id: str, meta) -> None:
    if isinstance(meta, dict):
        db.add(ExtractionEvent(job_id=linkedin_id, strategies=meta.get("strategies"),
                               failed_fields=meta.get("failed_fields")))


def capture(db, body: dict, classify) -> dict:
    """The same body the extension sends to the local /api/extension/jobs.
    Raises ValueError when job_id, title or raw_text is missing."""
    linkedin_id, title, raw_text = str(body.get("job_id") or ""), body.get("title"), body.get("raw_text")
    if not (linkedin_id and title and raw_text):
        raise ValueError("job_id, title and raw_text are required")
    override = body.get("track_override") if body.get("track_override") in ("ml_ai", "pm") else None
    track = override or classify_track(title)
    if track is None:
        return {"status": "needs_track", "title": title, "company_name": body.get("company")}

    job = save_new_job(db, keyword=body.get("keyword") or "extension", track=track, job_id=linkedin_id,
                       detail={field: body.get(field) for field in DETAIL_FIELDS})
    _record_extraction_event(db, linkedin_id, body.get("extraction_meta"))
    if is_agency_job(job):
        return {"status": "blocked", "reason": "agency", "job": job_summary(job)}
    if job.duplicate_of_job_id:
        return {"status": "duplicate", "duplicate_of": job.duplicate_of_job_id, "job": job_summary(job)}

    seniority = _seniority(db, job)
    if seniority is None:
        try:
            with Timer() as timer:
                result = classify(format_posting(job))
            metric("SeniorityClassifyMs", timer.ms, unit="Milliseconds")
        except Exception as exc:   # the capture is kept; the backfill classifies it later
            metric("SeniorityClassifyFailed")
            log_event("seniority_classify_failed", level="warning", job_id=job.id, error_type=type(exc).__name__)
            result = None
        if result is not None:
            seniority = JobSeniority(job_id=job.id, level=result.level, is_contract=result.is_contract,
                                     years_required=result.years_required, inferred=result.inferred,
                                     confidence=result.confidence, evidence=result.evidence, note=result.note,
                                     prompt_version=PROMPT_VERSION)
            db.add(seniority)
    db.flush()
    return {"status": "scored" if seniority else "saved", "job": job_summary(job), "seniority": seniority_view(seniority)}


def cached_capture(db, linkedin_id: str) -> dict | None:
    job = db.query(Job).filter(Job.job_id == linkedin_id).one_or_none()
    return None if job is None else {"status": "cached", "job": job_summary(job), "seniority": seniority_view(_seniority(db, job))}


def agency_list() -> dict:
    return {"name_substrings": list(AGENCY_COMPANY_NAME_SUBSTRINGS), "industries": ["Staffing and Recruiting"]}


PAGE_INT_FIELDS = ("page", "rendered", "skipped", "clicked")


def record_collection_page(db, body: dict) -> dict:
    """Raises ValueError for missing or malformed fields."""
    if not isinstance(body.get("session_id"), str) or not isinstance(body.get("keyword"), str):
        raise ValueError("session_id and keyword are required")
    if not all(isinstance(body.get(f), int) and not isinstance(body.get(f), bool) for f in PAGE_INT_FIELDS):
        raise ValueError(f"{', '.join(PAGE_INT_FIELDS)} must be whole numbers")
    planned = body.get("pages_planned")
    page = CollectionPage(session_id=body["session_id"], keyword=body["keyword"], endpoint=body.get("endpoint"),
                          page=body["page"], rendered=body["rendered"], skipped=body["skipped"],
                          clicked=body["clicked"], pages_planned=planned if isinstance(planned, int) else None)
    db.add(page)
    db.flush()
    return {"id": page.id}


def set_expired(db, job_id: int, expired) -> dict:
    """Raises ValueError (not a boolean) or LookupError (no such job)."""
    if not isinstance(expired, bool):
        raise ValueError("expired must be true or false")
    job = db.get(Job, job_id)
    if job is None:
        raise LookupError("job not found")
    job.expired = expired
    db.flush()
    return job_summary(job)


def set_plan(db, email, plan) -> dict:
    """Until payments exist, an admin sets a member's plan. Goes through the
    set_user_plan database function, the admin role's only way to change a
    users row it does not own. Raises ValueError or LookupError."""
    if plan not in PLANS:
        raise ValueError(f"plan must be one of {', '.join(PLANS)}")
    if not isinstance(email, str) or "@" not in email:
        raise ValueError("email required")
    user_id = db.execute(text("SELECT set_user_plan(:email, :plan)"), {"email": email.strip(), "plan": plan}).scalar()
    if user_id is None:
        raise LookupError("no such user")
    return {"email": email.strip().lower(), "plan": plan}


# --- public stats (no sign-in) --------------------------------------------------

PUBLIC_STATS_TTL = 300   # seconds; the landing page is cached at the edge anyway


def public_stats(db) -> dict:
    """Aggregates for the landing page: counts and freshness only, never a
    user's scores or tracking. Shared tables only, so any role may read it."""
    totals = db.execute(text("""
        SELECT count(*) FILTER (WHERE duplicate_of_job_id IS NULL AND raw_text IS NOT NULL) AS jobs,
               count(*) FILTER (WHERE duplicate_of_job_id IS NULL AND raw_text IS NOT NULL
                                AND workplace_type = 'Remote') AS remote,
               count(*) FILTER (WHERE duplicate_of_job_id IS NULL AND raw_text IS NOT NULL
                                AND first_seen_at > (now() AT TIME ZONE 'utc') - interval '1 day') AS today,
               max(first_seen_at) AS last_collected
        FROM jobs
    """)).mappings().one()
    levels = db.execute(text("SELECT count(*) FROM job_seniority")).scalar()
    companies = db.execute(text("SELECT count(*) FROM companies")).scalar()
    return {
        "jobs": totals["jobs"],
        "remote_jobs": totals["remote"],
        "collected_today": totals["today"],
        "jobs_with_level": levels,
        "companies": companies,
        "last_collected_at": totals["last_collected"].isoformat() if totals["last_collected"] else None,
    }
