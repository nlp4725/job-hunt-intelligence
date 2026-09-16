"""A user's scores on every job, computed in code from stored data, with no LLM
call (productization plan §3.3).

Skill Match compares each job's stored job_skills with the skills confirmed on
the user's active resume: the one their latest profile version points at.
Duplicate postings are not scored, the same as local screening. Seniority Fit
arrives in phase 4, and total_score with it.
"""

from collections import defaultdict

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert

from analysis.skill_match import skill_match_from_skills
from analysis.taxonomy_version import taxonomy_version
from db.cloud_models import UserJobScore, UserProfile, UserResume
from db.models import Job, JobSkill, utcnow

BATCH = 1000


def active_profile(db, user_id: int) -> UserProfile | None:
    return db.query(UserProfile).filter_by(user_id=user_id).order_by(UserProfile.version.desc()).first()


def score_user(db, user_id: int) -> int:
    """Upsert one user_job_scores row per scorable job; returns how many."""
    profile = active_profile(db, user_id)
    resume = db.get(UserResume, profile.resume_id) if profile and profile.resume_id else None
    if resume is None or resume.skills_confirmed is None:
        return 0
    resume_skills = set(resume.skills_confirmed)

    scorable = [Job.duplicate_of_job_id.is_(None), Job.raw_text.isnot(None)]
    skills_by_job: dict[int, set[str]] = defaultdict(set)
    for job_id, name in db.query(JobSkill.job_id, JobSkill.skill_name).join(Job, Job.id == JobSkill.job_id).filter(*scorable):
        skills_by_job[job_id].add(name)

    now, version = utcnow(), taxonomy_version()
    rows = []
    for (job_id,) in db.query(Job.id).filter(*scorable):
        match = skill_match_from_skills(skills_by_job.get(job_id, set()), resume_skills)
        rows.append({
            "user_id": user_id, "job_id": job_id, "profile_version": profile.version, "taxonomy_version": version,
            "skill_score": match["score"], "skill_ratio": match["ratio"], "skill_matched": match["matched_skills"],
            "skill_group_matched": match["group_matched_skills"], "skill_missing": match["missing_skills"],
            "seniority_fit": None, "total_score": None, "scored_at": now,
        })

    db.flush()
    for start in range(0, len(rows), BATCH):
        stmt = insert(UserJobScore).values(rows[start:start + BATCH])
        updates = {column: stmt.excluded[column] for column in rows[0] if column not in ("user_id", "job_id")}
        db.execute(stmt.on_conflict_do_update(constraint="uq_user_job_scores_user_job", set_=updates))
    # A job that has since become a duplicate (or lost its text) keeps no stale score.
    unscorable = db.query(Job.id).filter(or_(Job.duplicate_of_job_id.isnot(None), Job.raw_text.is_(None)))
    db.query(UserJobScore).filter(UserJobScore.user_id == user_id, UserJobScore.job_id.in_(unscorable)) \
        .delete(synchronize_session=False)
    db.expire_all()
    return len(rows)
