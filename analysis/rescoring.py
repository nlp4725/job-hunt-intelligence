"""Rescoring work, independent of how it is triggered (productization plan §1.5.9).

cloud_api/rescore.py publishes messages; the jhi-rescore Lambda
(cloud_api/lambda_handlers.py) calls process_message for each one and
reconcile every hour. Runs as a login in the jhi_scorer role (or the owner).

    {"type": "job",  "job_id": 123}   a captured job: score it for every ready user
    {"type": "user", "user_id": 42}   a profile or resume change: rescore that user's whole board
"""

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, or_, select

from analysis.user_scoring import SCORING_VERSION, active_profile, score_user
from db.cloud_models import User, UserJobScore, UserProfile, UserResume
from db.models import Job, utcnow

MESSAGE_TYPES = ("job", "user")
RECENT_DAYS = 3          # reconciliation looks for unscored jobs captured this recently


def job_message(job_id: int) -> dict:
    return {"type": "job", "job_id": int(job_id)}


def user_message(user_id: int) -> dict:
    return {"type": "user", "user_id": int(user_id)}


def ready_user_ids(db) -> list[int]:
    """Users whose latest profile points at a resume with confirmed skills."""
    latest = (select(UserProfile.user_id, func.max(UserProfile.version).label("version"))
              .group_by(UserProfile.user_id).subquery())
    rows = (db.query(UserProfile.user_id)
            .join(latest, (latest.c.user_id == UserProfile.user_id) & (latest.c.version == UserProfile.version))
            .join(UserResume, UserResume.id == UserProfile.resume_id)
            .join(User, User.id == UserProfile.user_id)
            .filter(UserResume.skills_confirmed.isnot(None), User.deleted_at.is_(None))
            .order_by(UserProfile.user_id))
    return [user_id for (user_id,) in rows]


@dataclass
class MessageResult:
    kind: str
    scores: int = 0
    missing: bool = False       # the job or user no longer exists (deleted): nothing to do


def process_message(db, message) -> MessageResult:
    """Do one message's scoring. Raises ValueError for a malformed message."""
    kind = message.get("type") if isinstance(message, dict) else None
    try:
        if kind == "job":
            job_id = int(message["job_id"])
            if db.get(Job, job_id) is None:
                return MessageResult(kind, missing=True)
            return MessageResult(kind, sum(score_user(db, user_id, job_ids=[job_id]) for user_id in ready_user_ids(db)))
        if kind == "user":
            user_id = int(message["user_id"])
            if db.get(User, user_id) is None:
                return MessageResult(kind, missing=True)
            return MessageResult(kind, score_user(db, user_id))
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed rescore message: {message!r}") from exc
    raise ValueError(f"unknown rescore message: {message!r}")


@dataclass
class ReconcileReport:
    stale_users: int = 0            # rescored in full: new profile or scoring logic since their last scores
    missing_scores: int = 0         # score rows written for recent jobs a lost message left unscored
    oldest_unscored_minutes: float = 0.0
    user_ids: list[int] = field(default_factory=list)


def reconcile(db, recent_days: int = RECENT_DAYS) -> ReconcileReport:
    """The safety net behind the queue. Commits after each user so progress survives a timeout."""
    report = ReconcileReport()
    since = utcnow() - timedelta(days=recent_days)
    for user_id in ready_user_ids(db):
        profile = active_profile(db, user_id)
        scores = db.query(UserJobScore.id).filter(UserJobScore.user_id == user_id)
        stale = scores.first() is None or scores.filter(or_(
            UserJobScore.profile_version != profile.version,
            UserJobScore.scoring_version.is_distinct_from(SCORING_VERSION))).first() is not None
        if stale:
            score_user(db, user_id)
            report.stale_users += 1
            report.user_ids.append(user_id)
            db.commit()
            continue
        has_score = select(UserJobScore.id).where(UserJobScore.user_id == user_id, UserJobScore.job_id == Job.id).exists()
        missing = (db.query(Job.id, Job.first_seen_at)
                   .filter(Job.duplicate_of_job_id.is_(None), Job.raw_text.isnot(None), Job.first_seen_at >= since, ~has_score)
                   .all())
        if missing:
            now = utcnow().replace(tzinfo=None)   # timestamps are stored as naive UTC
            oldest = min(seen.replace(tzinfo=None) for _, seen in missing)
            report.oldest_unscored_minutes = max(report.oldest_unscored_minutes, (now - oldest).total_seconds() / 60)
            report.missing_scores += score_user(db, user_id, job_ids=[job_id for job_id, _ in missing])
            report.user_ids.append(user_id)
            db.commit()
    return report


def readiness(db) -> dict:
    """Why the scorer has nobody to score, as counts rather than a guess.

    ready_user_ids is three joins and a NOT NULL, and when it returns nothing
    the reconcile logs a cheerful "0 stale, 0 missing" that looks identical to
    "everyone is scored". This counts each step of that chain so the gap is
    visible: no account, no profile, a profile pointing at no resume, or a
    resume whose skills were never confirmed. No emails, no resume content.
    """
    latest = (select(UserProfile.user_id, func.max(UserProfile.version).label("version"))
              .group_by(UserProfile.user_id).subquery())
    live_profiles = (db.query(UserProfile)
                     .join(latest, (latest.c.user_id == UserProfile.user_id) & (latest.c.version == UserProfile.version))
                     .join(User, User.id == UserProfile.user_id)
                     .filter(User.deleted_at.is_(None)))
    return {
        "users": db.query(User).filter(User.deleted_at.is_(None)).count(),
        "with_a_profile": live_profiles.count(),
        "profile_points_at_a_resume": live_profiles.filter(UserProfile.resume_id.isnot(None)).count(),
        "resume_skills_confirmed": live_profiles.join(UserResume, UserResume.id == UserProfile.resume_id)
                                                .filter(UserResume.skills_confirmed.isnot(None)).count(),
        "ready_to_score": len(ready_user_ids(db)),
        "users_with_any_score": db.query(UserJobScore.user_id).distinct().count(),
    }
