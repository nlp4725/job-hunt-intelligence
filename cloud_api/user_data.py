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


def set_request_user(db, user_id: int) -> None:
    """Tell Postgres whose request this transaction serves. Called by the auth
    guards right after the caller is verified; the value comes only from the
    verified user row and ends with the transaction."""
    db.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)})


def onboarding_state(db, user: User) -> dict:
    resumes = db.query(UserResume).filter(UserResume.user_id == user.id).all()
    profile = active_profile(db, user.id)
    return {
        "resume": bool(resumes),
        "skills_confirmed": any(r.skills_confirmed is not None for r in resumes),
        "level": profile is not None,
        "scores_confirmed": profile is not None and profile.seniority_scores is not None,
    }
