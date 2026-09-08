"""
Deterministic, free title-relevance filtering — same approach as
skills_extractor.py (curated keyword list + word-boundary regex, no LLM).

LinkedIn's `keywords=` search matches anywhere in a posting's full text, not
just the title, so a "machine learning" search also returns roles like
"Accounting Paid Consultant" or "Zoho Consultant" that only mention it in
passing. Title is available straight off the list page (see
scraper.linkedin_scraper.get_job_cards_on_page), so filtering happens
there, before a job ever gets a placeholder row or a full detail fetch.
"""

import re

TITLE_TERMS_BY_TRACK: dict[str, list[str]] = {
    "ml_ai": [
        "machine learning", r"\bml\b", r"\bai\b", "artificial intelligence",
        "deep learning", r"\bnlp\b", "computer vision", r"\bllm\b",
        "generative ai", r"\bgenai\b", "mlops",
        "data scientist", "data science", r"\bds\b",
        "ai solution", "ai engineer", "ai scientist",
        "applied scientist",
        "agentic", "forward deployed",
        "software", "scientist", "model", "engineer",
    ],
    "pm": [
        "product manager", "product owner", "product lead", "product management",
        "head of product", "director of product", "vp of product", "chief product officer",
    ],
}

_COMPILED_PATTERNS: dict[str, re.Pattern] = {
    track: re.compile(r"(?:" + "|".join(terms) + r")", re.IGNORECASE)
    for track, terms in TITLE_TERMS_BY_TRACK.items()
}


def is_relevant_title(title: str | None, track: str) -> bool:
    """True if `title` matches any of its track's curated terms. Unknown
    tracks (no curated list) pass everything through unfiltered rather than
    silently dropping jobs for a track we haven't defined terms for yet."""
    if not title:
        return False
    pattern = _COMPILED_PATTERNS.get(track)
    if pattern is None:
        return True
    return bool(pattern.search(title))


def classify_track(title: str | None) -> str | None:
    """Best-effort ml_ai vs pm classification for a title with no known
    search keyword — e.g. a job captured via the browser extension, where
    there's no KEYWORD_TRACKS lookup to fall back on. Checks "pm" first:
    its term list is a tight, low-false-positive set of product-management-
    specific phrases, whereas "ml_ai"'s list includes broad single words
    ("software", "engineer") that would false-positive on plenty of PM
    titles if checked first — e.g. "Product Manager in Software" contains
    "software", one of ml_ai's own terms. Returns None if neither track's
    terms match, leaving the caller to ask the user."""
    if not title:
        return None
    if _COMPILED_PATTERNS["pm"].search(title):
        return "pm"
    if _COMPILED_PATTERNS["ml_ai"].search(title):
        return "ml_ai"
    return None


# Director-level, Staff-level, and Principal-level (and above) roles are a
# seniority mismatch for the candidate's actual band (~3 years, mid-level —
# see judge/seniority_fit.py's bands) regardless of track, so this is
# checked independently of TITLE_TERMS_BY_TRACK rather than folded into it:
# a title can be track-relevant ("Director, Data Science") and still be the
# wrong seniority. Deterministic and applied at the same list-page stage as
# is_relevant_title, before a job ever gets a placeholder row or a full
# detail fetch — cheaper than waiting for Seniority Fit to score it 0/5
# after a full Screening Agent call.
_SENIOR_TITLE_PATTERN = re.compile(r"(?:\bdirector\b|\bstaff\b|\bprincipal\b)", re.IGNORECASE)


def is_too_senior_title(title: str | None) -> bool:
    """True if `title` reads as Director-level, Staff-level, Principal-level, or above."""
    if not title:
        return False
    return bool(_SENIOR_TITLE_PATTERN.search(title))
