"""Classify the seniority level of every scorable job that has none yet, then
rescore every user. One LLM call per job; the owner pays (productization plan).

    JHI_DATABASE_URL=postgresql+psycopg://... ./venv/bin/python -m db.classify_job_seniority --dry-run
    JHI_DATABASE_URL=postgresql+psycopg://... ./venv/bin/python -m db.classify_job_seniority [--limit N]

"Scorable" = not a duplicate, has JD text. Jobs that already have a
job_seniority row (including the backfill from local scores) are never sent
again, so a stopped run resumes where it left off: results are committed in
small batches as they arrive. A failed call is counted and skipped; re-running
retries it.
"""

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import create_engine, exists
from sqlalchemy.orm import Session

from analysis.user_scoring import active_profile, score_user
from db.cloud_models import JobSeniority, User
from db.models import Job
from judge.seniority_fit import format_posting
from judge.seniority_level import JobSeniorityLevel, classify_job_seniority

PROMPT_VERSION = "seniority_level_v1"
COMMIT_EVERY = 25

# DeepSeek V4 Pro, USD per 1M tokens (tests_and_eval/test_seniority/test_seniority_deepseek.py).
PRICE_PER_M_INPUT = 0.435
PRICE_PER_M_OUTPUT = 0.87
PROMPT_TOKENS = 2600          # SENIORITY_LEVEL_PROMPT, measured roughly as chars / 4
OUTPUT_TOKENS = 150


@dataclass
class ClassifyReport:
    classified: int = 0
    failed: int = 0
    rescored_users: int = 0


def _unclassified(db, limit: int | None):
    query = (db.query(Job)
             .filter(Job.duplicate_of_job_id.is_(None), Job.raw_text.isnot(None),
                     ~exists().where(JobSeniority.job_id == Job.id))
             .order_by(Job.first_seen_at.desc()))
    return query.limit(limit).all() if limit else query.all()


def estimate_cost_usd(postings: list[str]) -> float:
    """Upper bound: every prompt billed as fresh input (no cache discount)."""
    input_tokens = sum(PROMPT_TOKENS + len(p) / 4 for p in postings)
    return input_tokens / 1e6 * PRICE_PER_M_INPUT + len(postings) * OUTPUT_TOKENS / 1e6 * PRICE_PER_M_OUTPUT


def classify_missing(target_url: str, classify: Callable[[str], JobSeniorityLevel] = classify_job_seniority,
                     limit: int | None = None, workers: int = 8) -> ClassifyReport:
    engine = create_engine(target_url, connect_args={"options": "-c timezone=UTC"})
    report = ClassifyReport()
    try:
        with Session(engine) as db:
            postings = {job.id: format_posting(job) for job in _unclassified(db, limit)}

        pending: list[JobSeniority] = []

        def flush(rows: list[JobSeniority]) -> None:
            with Session(engine) as db, db.begin():
                db.add_all(rows)
            rows.clear()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(classify, posting): job_id for job_id, posting in postings.items()}
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception:
                    report.failed += 1
                    continue
                pending.append(JobSeniority(
                    job_id=futures[future], level=result.level, non_fit_reason=result.non_fit_reason,
                    years_required=result.years_required, inferred=result.inferred, confidence=result.confidence,
                    evidence=result.evidence, note=result.note, prompt_version=PROMPT_VERSION,
                ))
                report.classified += 1
                if len(pending) >= COMMIT_EVERY:
                    flush(pending)
        if pending:
            flush(pending)

        with Session(engine) as db, db.begin():
            for (user_id,) in db.query(User.id).order_by(User.id):
                if active_profile(db, user_id) is not None and score_user(db, user_id):
                    report.rescored_users += 1
    finally:
        engine.dispose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true", help="count jobs and estimate cost; no LLM calls")
    args = parser.parse_args()
    url = os.environ.get("JHI_DATABASE_URL")
    if not url:
        parser.error("set JHI_DATABASE_URL to the cloud Postgres database")

    if args.dry_run:
        engine = create_engine(url)
        with Session(engine) as db:
            postings = [format_posting(job) for job in _unclassified(db, args.limit)]
        engine.dispose()
        print(f"{len(postings)} jobs to classify · estimated at most ${estimate_cost_usd(postings):.2f}")
        return
    print(classify_missing(url, limit=args.limit, workers=args.workers))


if __name__ == "__main__":
    main()
