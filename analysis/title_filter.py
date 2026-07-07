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
