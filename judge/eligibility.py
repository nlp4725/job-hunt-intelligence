"""
Screening eligibility — shared by the batch screening run
(judge/screening_run.py) and the live per-job queue (scraper/run_scrape.py),
so a job is skipped by the exact same rule regardless of which path screens
it: an agency/staffing posting, a confirmed repost of an already-scraped job
(Job.duplicate_of_job_id, set at scrape time by
analysis/duplicate_detector.py), or a track with no resume to score against.
"""

from db.models import Job, Resume
from judge.agency_blocklist import is_agency_job


def resume_for_screening(
    job: Job, resumes_by_track: dict[str, str], default_content: str | None
) -> str | None:
    """Returns the resume content to screen `job` against, or None if it
    should be skipped entirely."""
    if is_agency_job(job):
        return None
    if job.duplicate_of_job_id is not None:
        return None
    return resumes_by_track.get(job.track, default_content)


def load_resumes(session) -> tuple[dict[str, str], str | None]:
    """Loads every Resume row once into {track: content}, plus a fallback
    (the first track=NULL row, if any) used for any track without its own
    resume yet."""
    resumes_by_track: dict[str, str] = {}
    default_content: str | None = None
    for r in session.query(Resume).all():
        if r.track:
            resumes_by_track[r.track] = r.content
        elif default_content is None:
            default_content = r.content
    return resumes_by_track, default_content
