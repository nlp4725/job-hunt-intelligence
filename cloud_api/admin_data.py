"""Admin-route logic on shared tables: captures, cached lookups, the agency
list, collection pages and expiry (productization plan §2, §6).

Runs as jhi_admin_api, which has no access to per-user tables. A capture only
queues its job in rescore_queue; cloud_api/rescore_worker.py scores it for users.
"""

from sqlalchemy.dialects.postgresql import insert

from analysis.title_filter import classify_track
from db.classify_job_seniority import PROMPT_VERSION
from db.cloud_models import JobSeniority, RescoreQueue
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
            result = classify(format_posting(job))
        except Exception:   # the capture is kept; the backfill classifies it later
            result = None
        if result is not None:
            seniority = JobSeniority(job_id=job.id, level=result.level, is_contract=result.is_contract,
                                     years_required=result.years_required, inferred=result.inferred,
                                     confidence=result.confidence, evidence=result.evidence, note=result.note,
                                     prompt_version=PROMPT_VERSION)
            db.add(seniority)
    db.execute(insert(RescoreQueue).values(job_id=job.id).on_conflict_do_nothing())
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
