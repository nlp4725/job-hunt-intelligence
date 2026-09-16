"""Paid tier: per-user Expertise Match over the board, one LLM call per
(user, job). Run by cloud_api/expertise_worker.py as the table owner.

Cost control: each run scores at most `limit` jobs per user, the user's best
Skill + Seniority matches first, and never rescores a job already scored
against the user's current expertise profile version.
"""

from dataclasses import dataclass

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert

from analysis.user_scoring import active_profile
from db.board_filters import not_agency
from db.cloud_models import ExpertiseProfile, User, UserJobExpertise, UserJobScore, UserResume
from db.models import Company, Job, utcnow
from judge.seniority_fit import format_posting


@dataclass
class ExpertiseReport:
    users: int = 0
    scored: int = 0
    failed: int = 0


def active_expertise_profile(db, user_id: int) -> ExpertiseProfile | None:
    return (db.query(ExpertiseProfile)
            .filter(ExpertiseProfile.user_id == user_id, ExpertiseProfile.confirmed_at.isnot(None))
            .order_by(ExpertiseProfile.version.desc()).first())


def profile_payload(row: ExpertiseProfile) -> dict:
    return {"summary": row.summary, "main_work": list(row.main_work), "dream": row.dream}


def score_user_expertise(db, user: User, scorer, limit: int, on_scored=None) -> ExpertiseReport:
    """scorer(resume_text, profile, posting_text) -> (match, final_score), as
    judge/user_expertise_match.score_user_expertise. Only the redacted resume
    text is sent. Commits after each job so a failure keeps the paid work done
    before it."""
    report = ExpertiseReport()
    expertise = active_expertise_profile(db, user.id)
    profile = active_profile(db, user.id)
    resume = db.get(UserResume, profile.resume_id) if profile and profile.resume_id else None
    if user.plan != "paid" or expertise is None or resume is None or not resume.redacted_text:
        return report
    report.users = 1

    jobs = (db.query(Job)
            .outerjoin(Company, Company.id == Job.company_id)
            .outerjoin(UserJobScore, and_(UserJobScore.job_id == Job.id, UserJobScore.user_id == user.id))
            .outerjoin(UserJobExpertise, and_(UserJobExpertise.job_id == Job.id, UserJobExpertise.user_id == user.id,
                                              UserJobExpertise.profile_version == expertise.version))
            .filter(Job.duplicate_of_job_id.is_(None), Job.raw_text.isnot(None), not_agency(),
                    UserJobExpertise.id.is_(None))
            .order_by(UserJobScore.total_score.desc().nullslast(), Job.first_seen_at.desc(), Job.id.desc())
            .limit(limit).all())
    payload = profile_payload(expertise)
    for job in jobs:
        try:
            match, final = scorer(resume.redacted_text, payload, format_posting(job))
        except Exception:  # noqa: BLE001  one bad call must not stop the user's run
            report.failed += 1
            continue
        row = {"user_id": user.id, "job_id": job.id, "profile_version": expertise.version,
               "domain_score": match.domain_score, "capability_score": match.capability_score,
               "dream_score": match.dream_score, "expertise_score": final, "scored_at": utcnow(),
               "evidence": {"domain": match.domain_evidence, "capability": match.capability_evidence,
                            "dream": match.dream_evidence, "note": match.note}}
        stmt = insert(UserJobExpertise).values(row)
        db.execute(stmt.on_conflict_do_update(
            constraint="uq_user_job_expertise_user_job",
            set_={k: stmt.excluded[k] for k in row if k not in ("user_id", "job_id")}))
        db.commit()
        report.scored += 1
        if on_scored:
            on_scored(user.id, job.id)
    return report
