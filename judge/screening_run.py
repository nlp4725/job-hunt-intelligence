"""
Screening Run — batch process that runs the Screening Agent (judge/stage1_screen.py)
against every job that doesn't have a ScreeningResult yet. See CONTEXT.md
"Screening Run". Eligibility (agency/staffing postings, confirmed reposts,
which resume to use) is decided by judge/eligibility.py, shared with the
live per-job screening queue in scraper/run_scrape.py — a job is skipped by
the exact same rule regardless of which path screens it. A duplicate stays
unscored rather than inheriting its original's ScreeningResult, so it simply
never appears in the dashboard's /api/jobs (an inner join on
ScreeningResult) — the same never-screened-so-never-shown mechanism
agency-blocked jobs already rely on.

This script remains useful as a standalone catch-up pass — e.g. backfilling
a DB that predates the live queue, or recovering from a run that was
interrupted before its queue drained.

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

from db.models import Job, ScreeningResult
from db.session import get_session
from judge.eligibility import load_resumes, resume_for_screening
from judge.stage1_screen import screen_job

MAX_WORKERS = 8


def screen_one(job_id: int, resume_content: str) -> tuple[int, str | None]:
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


def run_screening(track: str | None = None) -> None:
    """track: if given ('ml_ai' or 'pm'), only screens jobs on that track.
    Each job is scored against its own track's Resume row (Resume.track) so
    ml_ai and pm jobs — which need genuinely different resume content to
    score skill/expertise fit correctly — never get cross-matched against
    the wrong resume. A Resume row with track=NULL is the fallback used for
    any track that doesn't have its own row yet."""
    session = get_session()
    try:
        resumes_by_track, default_content = load_resumes(session)
        if not resumes_by_track and default_content is None:
            raise RuntimeError("No resume in DB — nothing to screen against.")

        already_screened = {row[0] for row in session.query(ScreeningResult.job_id).all()}
        query = (
            session.query(Job)
            .filter(Job.raw_text.isnot(None))
            .filter(~Job.id.in_(already_screened))
        )
        if track:
            query = query.filter(Job.track == track)
        candidates = query.all()

        job_resume_pairs: list[tuple[int, str]] = []
        for job in candidates:
            resume_content = resume_for_screening(job, resumes_by_track, default_content)
            if resume_content is None:
                continue  # agency posting, confirmed duplicate/repost, or no resume for this job's track — see judge/eligibility.py
            job_resume_pairs.append((job.id, resume_content))
        blocked_count = len(candidates) - len(job_resume_pairs)
    finally:
        session.close()

    total = len(job_resume_pairs)
    print(
        f"Screening {total} jobs ({len(already_screened)} already screened, "
        f"{blocked_count} agency/staffing postings blocked or missing a track resume, skipped)..."
    )

    start = time.perf_counter()
    done = 0
    errors = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(screen_one, jid, rc): jid for jid, rc in job_resume_pairs}
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
