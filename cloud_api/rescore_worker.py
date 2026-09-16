"""Score newly captured jobs for every user.

    JHI_DATABASE_URL=<owner url> ./venv/bin/python -m cloud_api.rescore_worker --once
    JHI_DATABASE_URL=<owner url> ./venv/bin/python -m cloud_api.rescore_worker --every 30

The capture route runs as jhi_admin_api and cannot write any user's rows, so it
only queues jobs in rescore_queue. This worker connects as the table owner
(row-level security does not apply to the owner), scores each queued job for
every user with a confirmed resume, in code with no LLM call, and removes the
job from the queue in the same transaction. Rows are claimed with SKIP LOCKED,
so two workers never score the same batch.
"""

import argparse
import time
from dataclasses import dataclass

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from analysis.user_scoring import score_user
from cloud_api.settings import owner_database_url
from db.cloud_models import RescoreQueue, User


@dataclass
class RescoreReport:
    jobs: int = 0
    scores: int = 0


def drain_rescore_queue(owner_url: str, batch: int = 200) -> RescoreReport:
    engine = create_engine(owner_url, connect_args={"options": "-c timezone=UTC"})
    report = RescoreReport()
    try:
        with Session(engine) as db, db.begin():
            job_ids = [job_id for (job_id,) in db.query(RescoreQueue.job_id).order_by(RescoreQueue.queued_at)
                       .limit(batch).with_for_update(skip_locked=True)]
            if not job_ids:
                return report
            for (user_id,) in db.query(User.id).order_by(User.id).all():
                report.scores += score_user(db, user_id, job_ids=job_ids)
            db.query(RescoreQueue).filter(RescoreQueue.job_id.in_(job_ids)).delete(synchronize_session=False)
            report.jobs = len(job_ids)
    finally:
        engine.dispose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--every", type=int, default=30, help="seconds between drains when not --once")
    args = parser.parse_args()
    try:
        url = owner_database_url()
    except RuntimeError:
        parser.error("set JHI_DATABASE_URL, or DB_HOST/DB_NAME/DB_OWNER_USER/DB_OWNER_PASSWORD, to the owner connection")
    while True:
        report = drain_rescore_queue(url)
        print(report, flush=True)
        if args.once:
            return
        if report.jobs == 0:
            time.sleep(args.every)


if __name__ == "__main__":
    main()
