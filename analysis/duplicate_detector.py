"""
Duplicate-listing detection — see CONTEXT.md-style design discussion: a
repost of the same real opening (same company rescrapes the same JD under a
new LinkedIn job_id, sometimes weeks apart) is common enough that a chunk of
"new" jobs each scrape are really the same handful of roles reappearing.

Matching signal is company_name (scope) + fuzzy JD-text similarity (match),
not title: title alone is both too weak (same company can have multiple
genuinely different reqs sharing a generic title like "Machine Learning
Engineer") and too easily defeated by trivial rewording. JD text is what's
actually copy-pasted between reposts, confirmed by inspecting a real
same-company pair (two Teradata "Senior AI Engineer, Agentic Systems"
postings, scraped a day apart under different job_ids): 99.77% similar, not
byte-identical — scrape/formatting noise rules out exact hashing, hence
difflib fuzzy matching with a threshold below 1.0 but close to it.
"""

import difflib

from db.models import Job

SIMILARITY_THRESHOLD = 0.95


def find_duplicate_job(session, company_name: str | None, raw_text: str | None, exclude_job_id: int) -> int | None:
    """Returns the id of the earliest existing job at the same company whose
    JD text is a near-exact match to `raw_text`, or None if there isn't one.
    Scoped to company_name first — comparing JD text pairwise across the
    whole corpus is wasteful and risks false positives between unrelated
    companies; within one company it's cheap and correct. exclude_job_id
    keeps a job from matching itself on a re-save."""
    if not company_name or not raw_text:
        return None

    candidates = (
        session.query(Job)
        .filter(Job.company_name == company_name)
        .filter(Job.id != exclude_job_id)
        .filter(Job.raw_text.isnot(None))
        .order_by(Job.first_seen_at.asc())
        .all()
    )

    for candidate in candidates:
        ratio = difflib.SequenceMatcher(None, candidate.raw_text, raw_text).ratio()
        if ratio >= SIMILARITY_THRESHOLD:
            # candidates are visited oldest-first and already had this same
            # check run at their own save time, so a candidate that's itself
            # a duplicate points at the true original one hop up — follow it
            # rather than chaining duplicate-of-duplicate references.
            return candidate.duplicate_of_job_id or candidate.id

    return None
