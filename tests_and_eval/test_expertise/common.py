"""
Shared rubric prompt, output schema, and job-fetch helper for the
expertise_match model comparison — mirrors tests_and_eval/test_seniority/common.py.
test_expertise_claude.py and test_expertise_deepseek.py both import from
here so they score the exact same prompt against the exact same job.

The prompt/schema themselves live in judge/expertise_match.py (the
production module) — re-exported here rather than duplicated, so the eval
harness and production are always testing the same rubric, never a stale
copy of it.
"""

from dotenv import load_dotenv

from db.models import Job
from db.session import get_session
from judge.expertise_match import EXPERTISE_MATCH_PROMPT, ExpertiseMatch, format_posting

load_dotenv()

# Real job pulled from data/job_hunt.db: "Sr Machine Learning Scientist" @
# Amgen — pharma domain (D3) but the core job is training foundational /
# diffusion models from scratch (W3), despite the domain match. Good
# smoke-test case since it exercises rule 2 (a W-item overriding a D match),
# not just a trivial full match.
JOB_ID = 5

__all__ = ["EXPERTISE_MATCH_PROMPT", "ExpertiseMatch", "JOB_ID", "get_job_posting"]


def get_job_posting(job_id: int = JOB_ID) -> str:
    session = get_session()
    try:
        return format_posting(session.get(Job, job_id))
    finally:
        session.close()
