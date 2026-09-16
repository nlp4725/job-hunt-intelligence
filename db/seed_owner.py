"""Make the owner user #1 in a freshly seeded cloud database.

    JHI_DATABASE_URL=postgresql+psycopg://... ./venv/bin/python -m db.seed_owner --email you@example.com --name "Nasi"

Run once, after `alembic upgrade head` and `python -m db.copy_to_cloud`. Cloud
only; the local SQLite file is not involved.

- users: the owner as id 1, role admin; idp_subject stays NULL until their first login claims the row
- user_profiles: version 1, seniority target "entry" and its proposed score table, which reproduces today's scores
- job_tracking: every job the owner applied to, passed on or wrote a note on. Those fields are then
  cleared on the shared jobs rows, so in the cloud a job's status lives only in job_tracking
- application_events: an "applied" event wherever applied_at is known
- job_seniority: levels backfilled from screening_results seniority scores 1–5 (5 → entry … 1 → staff;
  3 at low confidence is the rubric's "nothing inferable" → level NULL). Score 0 mixes principal with
  agency / contract / internship, so those jobs get no row until they are re-classified (phase 4)
"""

import argparse
import os
from dataclasses import dataclass

from sqlalchemy import and_, create_engine, or_, text
from sqlalchemy.orm import Session

from analysis.seniority_fit import proposed_scores
from db.cloud_models import ApplicationEvent, JobSeniority, JobTracking, User, UserProfile
from db.models import Job, ScreeningResult

SCORE_TO_LEVEL = {5: "entry", 4: "mid", 3: "senior", 2: "senior_plus", 1: "staff"}
BACKFILL_PROMPT_VERSION = "backfill:seniority_fit_score"


@dataclass
class SeedReport:
    tracking_rows: int = 0
    applied_events: int = 0
    seniority_rows: int = 0
    seniority_waiting: int = 0   # score-0 jobs left for re-classification


def _has_text(column):
    return and_(column.isnot(None), column != "")


def seed_owner(target_url: str, email: str, display_name: str | None = None) -> SeedReport:
    if target_url.startswith("sqlite"):
        raise RuntimeError("seed_owner runs against the cloud Postgres database only.")
    engine = create_engine(target_url, connect_args={"options": "-c timezone=UTC"})
    report = SeedReport()
    try:
        with Session(engine) as db, db.begin():
            if db.query(User).count():
                raise RuntimeError("The cloud database already has users: the owner is seeded once.")
            db.add(User(id=1, email=email, display_name=display_name, role="admin"))
            db.flush()
            db.execute(text("SELECT setval(pg_get_serial_sequence('users', 'id'), (SELECT MAX(id) FROM users))"))
            db.add(UserProfile(user_id=1, version=1, seniority_target="entry", seniority_scores=proposed_scores("entry")))

            touched = or_(Job.applied.is_(True), Job.not_interested.is_(True), _has_text(Job.note),
                          _has_text(Job.not_interested_note), _has_text(Job.applied_resume_version))
            for job in db.query(Job).filter(touched):
                db.add(JobTracking(
                    user_id=1, job_id=job.id, applied=job.applied, applied_at=job.applied_at,
                    applied_resume_version=job.applied_resume_version or None, not_interested=job.not_interested,
                    not_interested_note=job.not_interested_note or None, note=job.note or None,
                ))
                report.tracking_rows += 1
                if job.applied and job.applied_at:
                    db.add(ApplicationEvent(user_id=1, job_id=job.id, stage="applied", occurred_at=job.applied_at))
                    report.applied_events += 1
                job.applied, job.applied_at, job.applied_resume_version = False, None, None
                job.not_interested, job.not_interested_note, job.note = False, None, None
            # A cleared text field saved as "" is not a status, but it must not
            # linger on the shared row either.
            for column in (Job.note, Job.not_interested_note, Job.applied_resume_version):
                db.query(Job).filter(column == "").update({column: None}, synchronize_session=False)

            for result in db.query(ScreeningResult).filter(ScreeningResult.seniority_score.isnot(None)):
                if result.seniority_score not in SCORE_TO_LEVEL:
                    report.seniority_waiting += 1
                    continue
                nothing_inferable = result.seniority_score == 3 and result.seniority_confidence == "low"
                db.add(JobSeniority(
                    job_id=result.job_id,
                    level=None if nothing_inferable else SCORE_TO_LEVEL[result.seniority_score],
                    years_required=result.seniority_years_required, inferred=result.seniority_inferred,
                    confidence=result.seniority_confidence, evidence=result.seniority_evidence,
                    note=result.seniority_note, prompt_version=BACKFILL_PROMPT_VERSION,
                ))
                report.seniority_rows += 1
    finally:
        engine.dispose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", dest="display_name")
    args = parser.parse_args()
    url = os.environ.get("JHI_DATABASE_URL")
    if not url:
        parser.error("set JHI_DATABASE_URL to the cloud Postgres database")
    print(seed_owner(url, args.email, args.display_name))


if __name__ == "__main__":
    main()
