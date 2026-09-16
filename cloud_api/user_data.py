"""The access layer (productization plan §5.2, layer 2).

The only place cloud_api reads or writes per-user tables (resumes,
user_profiles, user_job_scores, job_tracking, application_events). Every
function takes the caller and filters on them; test_cloud_isolation.py fails if
any other cloud_api module uses those models.

Underneath, Postgres row-level security (layer 3) enforces the same rule for
the request's transaction, from the user id set by set_request_user.
"""

from sqlalchemy import text

from analysis.user_scoring import active_profile
from db.cloud_models import User, UserResume
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

