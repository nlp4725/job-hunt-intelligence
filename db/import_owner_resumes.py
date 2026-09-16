"""Import the owner's local resumes as user #1's resume versions, then score them.

    JHI_DATABASE_URL=postgresql+psycopg://... JHI_RESUME_KEY=... \\
        ./venv/bin/python -m db.import_owner_resumes --store-dir /path/to/resume-files

Run once, after db.seed_owner. Reads the shared `resume` table that
db.copy_to_cloud brought over (one extracted-text resume per track). A cloud
user has one active resume, so the ml_ai resume is imported last and becomes
active; the others stay as earlier versions. Skills are confirmed as extracted.

The report compares the owner's new scores with today's local screening scores
on ml_ai jobs. They differ where stored job_skills predate the current taxonomy
or normalize() (not re-extracted yet), since local scoring re-reads JD text.
"""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path, PurePath

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.cloud_models import User, UserJobScore, UserResume
from db.models import Job, Resume, ScreeningResult
from resume.ingest import add_resume, confirm_skills
from resume.store import LocalFileStore, ResumeCipher

OWNER_ID = 1


@dataclass
class ImportReport:
    resumes: int = 0
    scored_jobs: int = 0
    compared_with_local: int = 0   # ml_ai jobs that also have a local skill score
    same_as_local: int = 0


def import_owner_resumes(target_url: str, store, cipher: ResumeCipher) -> ImportReport:
    engine = create_engine(target_url, connect_args={"options": "-c timezone=UTC"})
    report = ImportReport()
    try:
        with Session(engine) as db, db.begin():
            owner = db.get(User, OWNER_ID)
            if owner is None:
                raise RuntimeError("Seed the owner first: python -m db.seed_owner")
            if db.query(UserResume).filter_by(user_id=OWNER_ID).count():
                raise RuntimeError("The owner already has resumes: they are imported once.")

            for local in sorted(db.query(Resume).all(), key=lambda r: (r.track == "ml_ai", r.id)):
                # The local table holds text already extracted from the PDF.
                name = f"{PurePath(local.original_filename or 'resume').stem}.txt"
                resume = add_resume(db, owner, name, local.content.encode(), store, cipher)
                confirm_skills(db, OWNER_ID, resume.id, resume.skills_extracted)
                report.resumes += 1

            report.scored_jobs = db.query(UserJobScore).filter_by(user_id=OWNER_ID).count()
            pairs = (db.query(UserJobScore.skill_score, ScreeningResult.skill_score)
                     .join(ScreeningResult, ScreeningResult.job_id == UserJobScore.job_id)
                     .join(Job, Job.id == UserJobScore.job_id)
                     .filter(UserJobScore.user_id == OWNER_ID, Job.track == "ml_ai", ScreeningResult.skill_score.isnot(None))
                     .all())
            report.compared_with_local = len(pairs)
            report.same_as_local = sum(cloud == local for cloud, local in pairs)
    finally:
        engine.dispose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store-dir", type=Path, required=True, help="where encrypted resume files are written")
    args = parser.parse_args()
    url = os.environ.get("JHI_DATABASE_URL")
    if not url:
        parser.error("set JHI_DATABASE_URL to the cloud Postgres database")
    print(import_owner_resumes(url, LocalFileStore(args.store_dir), ResumeCipher.from_env()))


if __name__ == "__main__":
    main()
