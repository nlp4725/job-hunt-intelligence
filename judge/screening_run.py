"""
Screening Run — batch process that runs the Screening Agent (judge/stage1_screen.py)
against every job that doesn't have a ScreeningResult yet. See CONTEXT.md
"Screening Run". Jobs on the Agency Blocklist (judge/agency_blocklist.py)
are filtered out before screening — never sent to the Screening Agent at all.

Concurrency: screen_job() already runs seniority_fit + expertise_match
concurrently for one job (2-way). This script adds an outer pool across
JOBS too — screening ~2,850 jobs serially at ~5-9s/job would take hours;
running several jobs at once cuts wall-clock roughly proportionally. Kept
moderate (default 8 concurrent jobs = up to 16 concurrent DeepSeek calls)
to stay well clear of API rate limits rather than maximize raw throughput.

Failures are logged and skipped rather than aborting the whole run — one
bad job (malformed text, a transient API error) shouldn't lose progress on
the other few thousand.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from db.models import Job, Resume, ScreeningResult
from db.session import get_session
from judge.agency_blocklist import is_agency_job
from judge.stage1_screen import screen_job

MAX_WORKERS = 8


def _screen_one(job_id: int, resume_content: str) -> tuple[int, str | None]:
    """Runs in a worker thread — opens its own session (SQLAlchemy sessions
    aren't thread-safe to share) and a throwaway Resume-like object isn't
    needed since screen_job only reads resume.content."""
    session = get_session()
    try:
        job = session.get(Job, job_id)

        class _ResumeText:
            content = resume_content

        screen_job(job, _ResumeText(), session)
        return job_id, None
    except Exception as e:
        return job_id, f"{type(e).__name__}: {e}"
    finally:
        session.close()


def run_screening() -> None:
    session = get_session()
    try:
        resume = session.query(Resume).first()
        if resume is None:
            raise RuntimeError("No resume in DB — nothing to screen against.")
        resume_content = resume.content

        already_screened = {row[0] for row in session.query(ScreeningResult.job_id).all()}
        candidates = (
            session.query(Job)
            .filter(Job.raw_text.isnot(None))
            .filter(~Job.id.in_(already_screened))
            .all()
        )
        job_ids = [job.id for job in candidates if not is_agency_job(job)]
        blocked_count = len(candidates) - len(job_ids)
    finally:
        session.close()

    total = len(job_ids)
    print(
        f"Screening {total} jobs ({len(already_screened)} already screened, "
        f"{blocked_count} agency/staffing postings blocked, skipped)..."
    )

    start = time.perf_counter()
    done = 0
    errors = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_screen_one, jid, resume_content): jid for jid in job_ids}
        for future in as_completed(futures):
            job_id, error = future.result()
            done += 1
            if error:
                errors.append((job_id, error))
                print(f"  [{done}/{total}] job {job_id} FAILED: {error}")
            elif done % 25 == 0 or done == total:
                elapsed = time.perf_counter() - start
                rate = done / elapsed
                eta_min = (total - done) / rate / 60 if rate > 0 else float("inf")
                print(f"  [{done}/{total}] {elapsed:.0f}s elapsed, ~{eta_min:.1f}min remaining")

    elapsed = time.perf_counter() - start
    print(f"Done in {elapsed / 60:.1f}min. {total - len(errors)} succeeded, {len(errors)} failed.")
    if errors:
        print("Failed job IDs:", [jid for jid, _ in errors])


if __name__ == "__main__":
    run_screening()
