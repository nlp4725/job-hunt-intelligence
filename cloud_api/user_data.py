"""The access layer (productization plan §5.2, layer 2).

The only place cloud_api reads or writes per-user tables (resumes,
user_profiles, user_job_scores, job_tracking, application_events). Every
function takes the caller and filters on them; test_cloud_isolation.py fails if
any other cloud_api module uses those models.

Underneath, Postgres row-level security (layer 3) enforces the same rule for
the request's transaction, from the user id set by set_request_user.
"""

from datetime import datetime

from sqlalchemy import and_, func, or_, text

from analysis.seniority_fit import proposed_scores
from analysis.user_scoring import active_profile, set_seniority_scores, set_seniority_target
from db.cloud_models import (
    APPLICATION_STAGES, ApplicationEvent, JobSeniority, JobTracking, User, UserJobScore, UserProfile, UserResume,
)
from db.models import Company, Job, utcnow
from judge.agency_blocklist import AGENCY_COMPANY_NAME_SUBSTRINGS
from resume import ingest


def set_request_user(db, user_id: int) -> None:
    """Tell Postgres whose request this transaction serves. Called by the auth
    guards right after the caller is verified; the value comes only from the
    verified user row and ends with the transaction."""
    db.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)})


def onboarding_state(db, user: User) -> dict:
    resumes = _processed_resumes(db, user)
    profile = active_profile(db, user.id)
    return {
        "resume": bool(resumes),
        "skills_confirmed": any(r.skills_confirmed is not None for r in resumes),
        "level": profile is not None,
        "scores_confirmed": profile is not None and profile.seniority_scores is not None,
    }



def _processed_resumes(db, user: User) -> list[UserResume]:
    """A version counts once its upload has been processed; a pending one does not."""
    return (db.query(UserResume)
            .filter(UserResume.user_id == user.id, UserResume.skills_extracted.isnot(None))
            .order_by(UserResume.version).all())


def _own_resume(db, user: User, resume_id: int) -> UserResume:
    row = db.query(UserResume).filter(UserResume.id == resume_id, UserResume.user_id == user.id).one_or_none()
    if row is None or row.skills_extracted is None:
        raise LookupError("resume not found")
    return row


def resume_view(row: UserResume) -> dict:
    return {"id": row.id, "version": row.version, "filename": row.original_filename,
            "skills_extracted": row.skills_extracted, "skills_confirmed": row.skills_confirmed,
            "uploaded_at": row.uploaded_at.isoformat() if row.uploaded_at else None}


def start_resume_upload(db, user: User, filename: str, storage):
    return ingest.start_resume_upload(db, user, filename, storage)


def finish_resume_upload(db, user: User, version: int, storage, cipher) -> dict:
    return resume_view(ingest.finish_resume_upload(db, user, version, storage, cipher))


def list_resumes(db, user: User) -> dict:
    profile = active_profile(db, user.id)
    return {"versions": [resume_view(r) for r in _processed_resumes(db, user)],
            "active_resume_id": profile.resume_id if profile else None}


def confirm_resume_skills(db, user: User, resume_id: int, skills: list[str]) -> dict:
    _own_resume(db, user, resume_id)
    return resume_view(ingest.confirm_skills(db, user.id, resume_id, skills))


def resume_download_link(db, user: User, resume_id: int, storage) -> str:
    row = _own_resume(db, user, resume_id)
    return storage.presign_download(row.storage_key, row.original_filename or "resume")


# --- profile -----------------------------------------------------------------

def profile_view(profile: UserProfile | None) -> dict | None:
    if profile is None:
        return None
    return {"version": profile.version, "seniority_target": profile.seniority_target,
            "seniority_scores": profile.seniority_scores, "proposed_scores": proposed_scores(profile.seniority_target),
            "target_roles": profile.target_roles, "note": profile.note, "resume_id": profile.resume_id}


def get_profile(db, user: User) -> dict:
    return {"profile": profile_view(active_profile(db, user.id))}


def pick_level(db, user: User, level) -> dict:
    """Raises ValueError for an unknown level."""
    return profile_view(set_seniority_target(db, user.id, str(level)))


def confirm_scores(db, user: User, scores) -> dict:
    """Raises ValueError for an invalid table, or when no level has been picked."""
    if not isinstance(scores, dict):
        raise ValueError("scores must be an object of level -> score")
    if active_profile(db, user.id) is None:
        raise ValueError("pick a level first")
    return profile_view(set_seniority_scores(db, user.id, scores))


# --- job board ----------------------------------------------------------------

def tracking_view(row: JobTracking | None) -> dict | None:
    if row is None:
        return None
    return {"applied": row.applied, "applied_at": row.applied_at.isoformat() if row.applied_at else None,
            "applied_resume_version": row.applied_resume_version, "not_interested": row.not_interested,
            "not_interested_note": row.not_interested_note, "note": row.note}


def _not_agency():
    """The same agency filter the local app applies before screening."""
    name = func.lower(func.coalesce(Job.company_name, ""))
    return and_(*(~name.contains(fragment, autoescape=True) for fragment in AGENCY_COMPANY_NAME_SUBSTRINGS),
                or_(Company.industry.is_(None), Company.industry != "Staffing and Recruiting"))


def job_board(db, user: User, limit: int = 100, offset: int = 0) -> list[dict]:
    """Shared jobs with the caller's own scores and tracking. Never returns raw_text."""
    applied_by_company = dict(
        db.query(Job.company_name, func.count(JobTracking.id))
        .join(JobTracking, JobTracking.job_id == Job.id)
        .filter(JobTracking.user_id == user.id, JobTracking.applied.is_(True))
        .group_by(Job.company_name).all())
    rows = (db.query(Job.id, Job.job_id, Job.title, Job.company_name, Job.location, Job.workplace_type,
                     Job.posted_date, Job.url, Job.first_seen_at, JobSeniority.level, JobSeniority.is_contract,
                     UserJobScore, JobTracking)
            .outerjoin(Company, Company.id == Job.company_id)
            .outerjoin(JobSeniority, JobSeniority.job_id == Job.id)
            .outerjoin(UserJobScore, and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user.id))
            .outerjoin(JobTracking, and_(JobTracking.job_id == Job.id, JobTracking.user_id == user.id))
            .filter(Job.duplicate_of_job_id.is_(None), Job.raw_text.isnot(None), _not_agency())
            .order_by(UserJobScore.total_score.desc().nullslast(), Job.first_seen_at.desc(), Job.id.desc())
            .limit(limit).offset(offset).all())
    board = []
    for r in rows:
        score = r.UserJobScore
        board.append({
            "id": r.id, "job_id": r.job_id, "title": r.title, "company": r.company_name, "location": r.location,
            "workplace_type": r.workplace_type, "posted_date": r.posted_date, "url": r.url,
            "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
            "level": r.level, "is_contract": r.is_contract,
            "scores": None if score is None else {
                "skill_score": score.skill_score, "seniority_fit": score.seniority_fit, "total_score": score.total_score,
                "skill_matched": score.skill_matched, "skill_group_matched": score.skill_group_matched,
                "skill_missing": score.skill_missing},
            "tracking": tracking_view(r.JobTracking),
            "company_applied_count": applied_by_company.get(r.company_name, 0),
        })
    return board


# --- tracking and applications ------------------------------------------------

TRACKING_FIELDS = {"applied": bool, "not_interested": bool, "not_interested_note": str, "note": str,
                   "applied_resume_version": str}


def save_tracking(db, user: User, job_id: int, changes: dict) -> dict:
    """Raises LookupError (no such job) or ValueError (bad field)."""
    for key, value in changes.items():
        kind = TRACKING_FIELDS.get(key)
        if kind is None or not (isinstance(value, kind) or (kind is str and value is None)):
            raise ValueError(f"invalid tracking field: {key}")
    if db.get(Job, job_id) is None:
        raise LookupError("job not found")
    row = db.query(JobTracking).filter(JobTracking.user_id == user.id, JobTracking.job_id == job_id).one_or_none()
    if row is None:
        row = JobTracking(user_id=user.id, job_id=job_id, applied=False, not_interested=False)
        db.add(row)
    newly_applied = changes.get("applied") is True and not row.applied
    for key, value in changes.items():
        setattr(row, key, value)
    if newly_applied:
        row.applied_at = utcnow()
        db.add(ApplicationEvent(user_id=user.id, job_id=job_id, stage="applied", occurred_at=row.applied_at))
    elif changes.get("applied") is False:
        row.applied_at = None
    db.flush()
    return tracking_view(row)


def event_view(event: ApplicationEvent) -> dict:
    return {"id": event.id, "stage": event.stage, "occurred_at": event.occurred_at.isoformat(), "note": event.note}


def add_application_event(db, user: User, job_id: int, stage, occurred_at=None, note=None) -> dict:
    """Raises ValueError (bad stage or date) or LookupError (no such job)."""
    if stage not in APPLICATION_STAGES:
        raise ValueError(f"stage must be one of {', '.join(APPLICATION_STAGES)}")
    when = datetime.fromisoformat(occurred_at) if occurred_at else utcnow()
    if db.get(Job, job_id) is None:
        raise LookupError("job not found")
    event = ApplicationEvent(user_id=user.id, job_id=job_id, stage=stage, occurred_at=when,
                             note=str(note) if note is not None else None)
    db.add(event)
    db.flush()
    return event_view(event)


def list_applications(db, user: User) -> list[dict]:
    events: dict[int, list] = {}
    for event in (db.query(ApplicationEvent).filter(ApplicationEvent.user_id == user.id)
                  .order_by(ApplicationEvent.occurred_at, ApplicationEvent.id)):
        events.setdefault(event.job_id, []).append(event_view(event))
    rows = (db.query(JobTracking, Job.title, Job.company_name)
            .join(Job, Job.id == JobTracking.job_id)
            .filter(JobTracking.user_id == user.id, JobTracking.applied.is_(True))
            .order_by(JobTracking.applied_at.desc().nullslast()).all())
    return [{"job_id": t.job_id, "title": title, "company": company, **tracking_view(t), "events": events.get(t.job_id, [])}
            for t, title, company in rows]


# --- account ------------------------------------------------------------------

def delete_account(db, user: User, storage) -> None:
    """Hard delete: the user's files, then the users row; the database removes
    every per-user row with it (ON DELETE CASCADE)."""
    if storage is not None:
        storage.delete_prefix(f"users/{user.id}/")
    db.delete(user)
    db.flush()

