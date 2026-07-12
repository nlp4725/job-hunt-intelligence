"""
Shared rubric prompt, output schema, and job-fetch helper for the
seniority_fit model comparison. test_seniority_claude.py and
test_seniority_deepseek.py both import from here so they score the exact
same prompt against the exact same job.

The prompt/schema themselves live in judge/seniority_fit.py (the
production module) — re-exported here rather than duplicated, so the eval
harness and production are always testing the same rubric, never a stale
copy of it.
"""

from dotenv import load_dotenv

from db.models import Job
from db.session import get_session
from judge.seniority_fit import SENIORITY_PROMPT, SeniorityFit, format_posting

load_dotenv()

# Real job pulled from data/job_hunt.db: "Senior AI Engineer" @ Tiger
# Analytics, with an explicit "5+ years of experience" requirement — a clean
# case (title and stated years agree) to check whether both models land on
# the same band from the same evidence before trying ambiguous postings.
JOB_ID = 188

__all__ = ["SENIORITY_PROMPT", "SeniorityFit", "JOB_ID", "get_job_posting"]


def get_job_posting(job_id: int = JOB_ID) -> str:
    session = get_session()
    try:
        return format_posting(session.get(Job, job_id))
    finally:
        session.close()
