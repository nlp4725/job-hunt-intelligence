"""
Agency Blocklist (see CONTEXT.md "Agency Blocklist") — two detection
methods, checked before a job reaches the Screening Agent:

1. A curated list of known data-labeling/AI-training gig platforms and
   freelance marketplaces, grounded in what's actually in the DB (found by
   inspecting real company_name values, not guessed) — these post generic
   contractor gigs ("AI Trainer", "Freelance ... Project") rather than
   direct roles at a real employer, and don't reliably trigger the
   seniority_fit/expertise_match rubrics' own recruiter-detection rule
   (that rule catches phrasing like "I'm partnered with...", which these
   postings don't use).
2. A deterministic check against Company.industry == "Staffing and
   Recruiting" (captured for most companies already in the DB).

This was previously documented in CONTEXT.md but never implemented —
screening_run.py screened every job regardless, so known agency/gig
postings ended up in screening_results with real scores.
"""

# Curated by inspecting real job listings in the DB (see judge/screening_run.py
# usage) — each of these was confirmed to post generic contractor/freelance
# "AI Trainer"-style gigs, not roles at a real, specific employer.
AGENCY_COMPANY_NAME_SUBSTRINGS = [
    "dataannotation",
    "turing",
    "alignerr",
    "yo it consulting",
    "toloka",
    "micro1",
    "mercor",
    "prolific",
    "meridial marketplace",  # "... by Invisible"
    "toptal",
    "scale ai",
    "telus digital",
    "braintrust",
    "handshake",
]


def is_agency_job(job) -> bool:
    """job: db.models.Job, with .company (relationship) loaded or loadable."""
    company_name = (job.company_name or "").lower()
    if any(needle in company_name for needle in AGENCY_COMPANY_NAME_SUBSTRINGS):
        return True

    if job.company is not None and job.company.industry == "Staffing and Recruiting":
        return True

    return False
