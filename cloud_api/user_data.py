"""The access layer (productization plan §5.2, layer 2).

The only place cloud_api reads or writes per-user tables (resumes,
user_profiles, user_job_scores, job_tracking, application_events). Every
function takes the caller and filters on them; test_cloud_isolation.py fails if
any other cloud_api module uses those models.

Underneath, Postgres row-level security (layer 3) enforces the same rule for
the request's transaction, from the user id set by set_request_user.
"""

from datetime import datetime, timedelta

from sqlalchemy import and_, func, text

from analysis.seniority_fit import proposed_scores
from analysis.user_expertise_scoring import active_expertise_profile
from analysis.user_scoring import active_profile, set_seniority_scores, set_seniority_target
from db.board_filters import not_agency
from db.cloud_models import (
    APPLICATION_STAGES, ApplicationEvent, ExpertiseProfile, JobSeniority, JobTracking, User, UserJobExpertise,
    UserJobScore, UserProfile, UserResume,
)
from db.models import Company, Job, utcnow
from resume import ingest


def set_request_user(db, user_id: int) -> None:
    """Tell Postgres whose request this transaction serves. Called by the auth
    guards right after the caller is verified; the value comes only from the
    verified user row and ends with the transaction."""
    db.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)})


def onboarding_state(db, user: User) -> dict:
    """The required steps, then Expertise Match: a paid step anyone may skip,
    which never holds up `complete`."""
    resumes = _processed_resumes(db, user)
    profile = active_profile(db, user.id)
    state = {
        "resume": bool(resumes),
        "skills_confirmed": any(r.skills_confirmed is not None for r in resumes),
        "level": profile is not None,
        "scores_confirmed": profile is not None and profile.seniority_scores is not None,
    }
    state["complete"] = all(state.values())
    # False while the rescore Lambda hasn't yet scored this profile version: the app shows "scoring your board…"
    state["board_scored"] = profile is not None and db.query(UserJobScore.id).filter(
        UserJobScore.user_id == user.id, UserJobScore.profile_version == profile.version).first() is not None
    state["expertise"] = expertise_step(db, user)
    return state


class PaidFeature(Exception):
    """A paid-member feature called on a free plan: 402."""


class TooManyDrafts(Exception):
    """Over the daily expertise draft limit: 429."""


def expertise_step(db, user: User) -> str:
    """"done" (a confirmed profile), "skipped", "locked" (free plan: upgrade or
    skip) or "pending" (paid, not done yet)."""
    if active_expertise_profile(db, user.id) is not None:
        return "done"
    if user.expertise_skipped_at is not None:
        return "skipped"
    return "pending" if user.plan == "paid" else "locked"


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
    return resume_view(ingest.confirm_skills(db, user.id, resume_id, skills, rescore=False))   # the route queues the rescore


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
    return profile_view(set_seniority_target(db, user.id, str(level), rescore=False))   # the route queues the rescore


def confirm_scores(db, user: User, scores) -> dict:
    """Raises ValueError for an invalid table, or when no level has been picked."""
    if not isinstance(scores, dict):
        raise ValueError("scores must be an object of level -> score")
    if active_profile(db, user.id) is None:
        raise ValueError("pick a level first")
    return profile_view(set_seniority_scores(db, user.id, scores, rescore=False))


# --- expertise profile (paid) ---------------------------------------------------

EXPERTISE_DRAFTS_PER_DAY = 3        # each draft is one ~$0.014 LLM call, 40-100s
EXPERTISE_TEXT_MAX = 600
EXPERTISE_MAIN_WORK_MAX = 8


def expertise_view(row: ExpertiseProfile | None) -> dict | None:
    if row is None:
        return None
    return {"version": row.version, "summary": row.summary, "main_work": row.main_work, "dream": row.dream,
            "confirmed": row.confirmed_at is not None, "created_at": row.created_at.isoformat()}


def _latest_draft(db, user: User, after_version: int) -> ExpertiseProfile | None:
    return (db.query(ExpertiseProfile)
            .filter(ExpertiseProfile.user_id == user.id, ExpertiseProfile.confirmed_at.is_(None),
                    ExpertiseProfile.version > after_version)
            .order_by(ExpertiseProfile.version.desc()).first())


def get_expertise(db, user: User) -> dict:
    active = active_expertise_profile(db, user.id)
    return {"plan": user.plan, "step": expertise_step(db, user), "profile": expertise_view(active),
            "draft": expertise_view(_latest_draft(db, user, active.version if active else 0))}


def _text(value, field: str, required: bool) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required")
    if len(value) > EXPERTISE_TEXT_MAX:
        raise ValueError(f"{field} is over {EXPERTISE_TEXT_MAX} characters")
    return value


def validate_expertise(profile) -> dict:
    """The same rules as judge/expertise_profile.validate_profile: summary and
    at least one main_work line required, dream optional."""
    sections = {"summary", "main_work", "dream"}
    if not isinstance(profile, dict) or set(profile) != sections:
        raise ValueError(f"profile must have exactly: {', '.join(sorted(sections))}")
    if not isinstance(profile["main_work"], list):
        raise ValueError("main_work must be a list")
    lines = [_text(v, "main_work", False) for v in profile["main_work"] if isinstance(v, str) and v.strip()]
    if not lines:
        raise ValueError("main_work needs at least one line")
    if len(lines) > EXPERTISE_MAIN_WORK_MAX:
        raise ValueError(f"at most {EXPERTISE_MAIN_WORK_MAX} main_work lines")
    return {"summary": _text(profile["summary"], "summary", True), "main_work": lines,
            "dream": _text(profile["dream"], "dream", False)}


def _paid_with_resume(db, user: User) -> UserResume:
    if user.plan != "paid":
        raise PaidFeature("Expertise Match is a paid-member feature")
    profile = active_profile(db, user.id)
    resume = db.get(UserResume, profile.resume_id) if profile and profile.resume_id else None
    if resume is None or resume.skills_confirmed is None:
        raise ValueError("upload and confirm a resume first")
    return resume


def _next_expertise_version(db, user: User) -> int:
    latest = db.query(func.max(ExpertiseProfile.version)).filter(ExpertiseProfile.user_id == user.id).scalar()
    return (latest or 0) + 1


def draft_expertise(db, user: User, drafter) -> dict:
    """One LLM call drafts summary and main_work from the redacted resume; the
    user edits it and writes dream. Raises PaidFeature, TooManyDrafts or ValueError."""
    resume = _paid_with_resume(db, user)
    since = utcnow() - timedelta(days=1)
    recent = (db.query(func.count(ExpertiseProfile.id))
              .filter(ExpertiseProfile.user_id == user.id, ExpertiseProfile.confirmed_at.is_(None),
                      ExpertiseProfile.created_at >= since).scalar())
    if recent >= EXPERTISE_DRAFTS_PER_DAY:
        raise TooManyDrafts(f"at most {EXPERTISE_DRAFTS_PER_DAY} drafts a day")
    drafted = drafter(resume.redacted_text or "")
    lines = [str(v).strip()[:EXPERTISE_TEXT_MAX] for v in drafted.get("main_work", []) if str(v).strip()]
    row = ExpertiseProfile(user_id=user.id, version=_next_expertise_version(db, user), resume_id=resume.id,
                           summary=str(drafted.get("summary", "")).strip()[:EXPERTISE_TEXT_MAX],
                           main_work=lines[:EXPERTISE_MAIN_WORK_MAX], dream="", confirmed_at=None)
    db.add(row)
    db.flush()
    return expertise_view(row)


def save_expertise(db, user: User, profile) -> dict:
    """Confirm the user's profile as a new version; the expertise worker scores
    jobs against it. Raises PaidFeature or ValueError."""
    resume = _paid_with_resume(db, user)
    cleaned = validate_expertise(profile)
    row = ExpertiseProfile(user_id=user.id, version=_next_expertise_version(db, user), resume_id=resume.id,
                           confirmed_at=utcnow(), **cleaned)
    db.add(row)
    user.expertise_skipped_at = None
    db.flush()
    return expertise_view(row)


def skip_expertise(db, user: User) -> dict:
    if user.expertise_skipped_at is None:
        user.expertise_skipped_at = utcnow()
        db.flush()
    return {"step": expertise_step(db, user)}


# --- job board ----------------------------------------------------------------

def tracking_view(row: JobTracking | None) -> dict | None:
    if row is None:
        return None
    return {"applied": row.applied, "applied_at": row.applied_at.isoformat() if row.applied_at else None,
            "applied_resume_version": row.applied_resume_version, "not_interested": row.not_interested,
            "not_interested_note": row.not_interested_note, "note": row.note}


def expertise_score_view(row: UserJobExpertise | None, active_version: int | None) -> dict | None:
    if row is None:
        return None
    return {"expertise_score": row.expertise_score, "domain": row.domain_score, "capability": row.capability_score,
            "dream": row.dream_score, "evidence": row.evidence, "stale": row.profile_version != active_version}


def job_board(db, user: User, limit: int = 100, offset: int = 0) -> list[dict]:
    """Shared jobs with the caller's own scores and tracking. Never returns raw_text.
    Expertise is shown next to total_score, not added to it; it is null for
    jobs not scored yet and for free plans."""
    active_expertise = active_expertise_profile(db, user.id)
    applied_by_company = dict(
        db.query(Job.company_name, func.count(JobTracking.id))
        .join(JobTracking, JobTracking.job_id == Job.id)
        .filter(JobTracking.user_id == user.id, JobTracking.applied.is_(True))
        .group_by(Job.company_name).all())
    rows = (db.query(Job.id, Job.job_id, Job.title, Job.company_name, Job.location, Job.workplace_type,
                     Job.posted_date, Job.url, Job.first_seen_at, JobSeniority.level, JobSeniority.is_contract,
                     UserJobScore, JobTracking, UserJobExpertise)
            .outerjoin(Company, Company.id == Job.company_id)
            .outerjoin(JobSeniority, JobSeniority.job_id == Job.id)
            .outerjoin(UserJobScore, and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user.id))
            .outerjoin(JobTracking, and_(JobTracking.job_id == Job.id, JobTracking.user_id == user.id))
            .outerjoin(UserJobExpertise, and_(UserJobExpertise.job_id == Job.id, UserJobExpertise.user_id == user.id))
            .filter(Job.duplicate_of_job_id.is_(None), Job.raw_text.isnot(None), not_agency())
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
            "expertise": (expertise_score_view(r.UserJobExpertise, active_expertise.version if active_expertise else None)
                          if user.plan == "paid" else None),
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

