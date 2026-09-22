"""Adopt the owner's already-paid-for screening as their per-user board scores.

    aws lambda invoke --function-name jhi-rescore --cli-binary-format raw-in-base64-out \\
        --payload '{"type": "adopt_local_scores"}' out.json && cat out.json

screening_results is the local single-user table: one row per job, carried up
by the seed and the backfill. The board reads user_job_scores, which is per
user × job and can only be computed — so a cloud full of imported screening
still showed an empty board.

Recomputing is free (set overlap and arithmetic, no LLM) but needs a resume in
the cloud to compare against. This is the other way round: take the scores that
were already paid for locally and write them as the owner's rows, so the board
works before any resume is uploaded.

Two things it deliberately does not do:

  * **No overwriting.** A job the owner already has a score for is left alone,
    so this can be re-run and can never clobber a real computed score.
  * **No inflated totals.** Locally total_score is out of 15 and includes
    Expertise; in the cloud it is Skill + Seniority Fit only (expertise is a
    separate column the paid worker fills). The total is recomputed from the
    two parts rather than copied, or the board would rank these above anything
    scored properly.

The rows are stamped with the owner's current profile version and the current
scoring version, which is what stops the hourly reconcile deciding they are
stale and spending the work to recompute them. That also means they last only
until the profile changes: uploading a resume bumps the version, and from then
on the real scorer takes over — which is the intended path, not a regression.
"""

from sqlalchemy import text

from analysis.taxonomy_version import taxonomy_version
from analysis.user_scoring import SCORING_VERSION

OWNER_SQL = text("SELECT id FROM users WHERE role = 'admin' AND deleted_at IS NULL ORDER BY id LIMIT 1")
PROFILE_SQL = text("SELECT version FROM user_profiles WHERE user_id = :user_id ORDER BY version DESC LIMIT 1")

ADOPT_SQL = text("""
    INSERT INTO user_job_scores (user_id, job_id, profile_version, taxonomy_version,
                                 skill_score, skill_ratio, skill_matched, skill_group_matched, skill_missing,
                                 seniority_fit, total_score, scored_at, scoring_version)
    SELECT :user_id, s.job_id, :profile_version, :taxonomy_version,
           s.skill_score, s.skill_ratio, s.skill_matched, s.skill_group_matched, s.skill_missing,
           s.seniority_score,
           CASE WHEN s.skill_score IS NULL OR s.seniority_score IS NULL
                THEN NULL ELSE s.skill_score + s.seniority_score END,
           COALESCE(s.screened_at, now() AT TIME ZONE 'utc'), :scoring_version
    FROM screening_results s
    JOIN jobs j ON j.id = s.job_id
    WHERE j.duplicate_of_job_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM user_job_scores u WHERE u.user_id = :user_id AND u.job_id = s.job_id)
""")


def adopt_local_scores(db) -> dict:
    """Write the owner's screening_results rows as user_job_scores. Idempotent."""
    user_id = db.execute(OWNER_SQL).scalar()
    if user_id is None:
        return {"adopted": 0, "reason": "no owner account"}
    profile_version = db.execute(PROFILE_SQL, {"user_id": user_id}).scalar()
    if profile_version is None:
        return {"adopted": 0, "reason": "the owner has no profile to attach scores to"}

    before = db.execute(text("SELECT count(*) FROM user_job_scores WHERE user_id = :u"), {"u": user_id}).scalar()
    adopted = db.execute(ADOPT_SQL, {"user_id": user_id, "profile_version": profile_version,
                                     "taxonomy_version": taxonomy_version(),
                                     "scoring_version": SCORING_VERSION}).rowcount
    db.commit()
    scorable = db.execute(text(
        "SELECT count(*) FROM screening_results s JOIN jobs j ON j.id = s.job_id "
        "WHERE j.duplicate_of_job_id IS NULL")).scalar()
    return {"user_id": user_id, "profile_version": profile_version, "adopted": adopted,
            "already_had": before, "eligible_screenings": scorable}
