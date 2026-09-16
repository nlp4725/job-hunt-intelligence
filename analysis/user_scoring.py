"""A user's scores on every job, computed in code from stored data, with no LLM
call (productization plan §3.3).

Skill Match compares each job's stored job_skills with the skills confirmed on
the user's active resume: the one their latest profile version points at.
Duplicate postings are not scored, the same as local screening. Seniority Fit
looks up the job's classified level (job_seniority) in the profile's confirmed
score table; total_score is Skill Match + Seniority Fit, left empty until the
job has a level.
"""

from collections import defaultdict

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert

from analysis.seniority_fit import proposed_scores, seniority_fit, validate_scores
from analysis.skill_match import skill_match_from_skills
from analysis.taxonomy_version import taxonomy_version
from db.cloud_models import JobSeniority, UserJobScore, UserProfile, UserResume
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
    levels = {job_id: (level, agency, contract) for job_id, level, agency, contract
              in db.query(JobSeniority.job_id, JobSeniority.level, JobSeniority.is_agency, JobSeniority.is_contract)}

    table = profile.seniority_scores or proposed_scores(profile.seniority_target)
    now, version = utcnow(), taxonomy_version()
    rows = []
    for (job_id,) in db.query(Job.id).filter(*scorable):
        match = skill_match_from_skills(skills_by_job.get(job_id, set()), resume_skills)
        fit = seniority_fit(*levels[job_id], table) if job_id in levels else None
        rows.append({
            "user_id": user_id, "job_id": job_id, "profile_version": profile.version, "taxonomy_version": version,
            "skill_score": match["score"], "skill_ratio": match["ratio"], "skill_matched": match["matched_skills"],
            "skill_group_matched": match["group_matched_skills"], "skill_missing": match["missing_skills"],
            "seniority_fit": fit, "total_score": None if fit is None else match["score"] + fit, "scored_at": now,
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


def set_seniority_scores(db, user_id: int, scores: dict, target: str | None = None) -> UserProfile:
    """Confirm a user's seniority score table: write it on a new profile version
    (the level they picked, if given, and everything else carried over) and
    rescore. Earlier versions keep their tables. No LLM call: job levels are
    already stored. Validates everything before writing anything."""
    scores = validate_scores(scores)
    current = active_profile(db, user_id)
    target = target or (current.seniority_target if current else None)
    proposed_scores(target)   # raises for an unknown level
    profile = UserProfile(
        user_id=user_id, version=(current.version + 1) if current else 1, seniority_target=target,
        seniority_scores=scores,
        resume_id=current.resume_id if current else None, target_roles=current.target_roles if current else None,
        note=current.note if current else None, years_experience=current.years_experience if current else None,
    )
    db.add(profile)
    db.flush()
    score_user(db, user_id)
    return profile


def set_seniority_target(db, user_id: int, target: str) -> UserProfile:
    """Save the level the user picked (onboarding, or later in Settings) on a
    new profile version, then rescore. Its score table starts unconfirmed
    (NULL), so scoring uses the proposal for this level until the next screen
    confirms one with set_seniority_scores. A table confirmed for an earlier
    level is not carried over. Validates before writing anything."""
    proposed_scores(target)   # raises for an unknown level
    current = active_profile(db, user_id)
    profile = UserProfile(
        user_id=user_id, version=(current.version + 1) if current else 1, seniority_target=target,
        seniority_scores=None,
        resume_id=current.resume_id if current else None, target_roles=current.target_roles if current else None,
        note=current.note if current else None, years_experience=current.years_experience if current else None,
    )
    db.add(profile)
    db.flush()
    score_user(db, user_id)
    return profile
