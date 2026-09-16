"""Paid tier: score Expertise Match for paid members (one LLM call per user and job).

    JHI_DATABASE_URL=<owner url> ./venv/bin/python -m cloud_api.expertise_worker --once --limit 50

Connects as the table owner: users can read their expertise scores but never
write them. See analysis/user_expertise_scoring.py for which jobs are scored.
"""

import argparse
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from analysis.user_expertise_scoring import ExpertiseReport, score_user_expertise
from cloud_api.settings import owner_database_url
from db.cloud_models import User


def _score_with_deepseek(resume_text, profile, posting_text):
    from judge.user_expertise_match import score_user_expertise as judge

    return judge(resume_text, profile, posting_text)


def run_expertise(owner_url: str, scorer=None, limit: int = 50) -> ExpertiseReport:
    engine = create_engine(owner_url, connect_args={"options": "-c timezone=UTC"})
    total = ExpertiseReport()
    try:
        with Session(engine) as db:
            paid = [user_id for (user_id,) in db.query(User.id)
                    .filter(User.plan == "paid", User.deleted_at.is_(None)).order_by(User.id)]
            for user_id in paid:
                report = score_user_expertise(db, db.get(User, user_id), scorer or _score_with_deepseek, limit)
                total.users += report.users
                total.scored += report.scored
                total.failed += report.failed
    finally:
        engine.dispose()
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=50, help="most jobs scored per user per run")
    parser.add_argument("--every", type=int, default=300, help="seconds between runs when not --once")
    args = parser.parse_args()
    try:
        url = owner_database_url()
    except RuntimeError:
        parser.error("set JHI_DATABASE_URL, or DB_HOST/DB_NAME/DB_OWNER_USER/DB_OWNER_PASSWORD, to the owner connection")
    while True:
        print(run_expertise(url, limit=args.limit), flush=True)
        if args.once:
            return
        time.sleep(args.every)


if __name__ == "__main__":
    main()
