"""
Resume text -> text safe to leave the skill extractor (logs, LLM calls,
embeddings). Skill extraction runs on the unredacted text, so this can err
towards over-redacting. See docs/resume_jd_skill_pipeline.md §4.2.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class KnownIdentity:
    """What the login token already tells us about the user."""
    name: str
    email: str


# Layer 2: found by pattern anywhere, so they're removed even when the resume
# uses a different email than the login, or no header is detected.
_PATTERNS = [
    ("[EMAIL]", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("[URL]", re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)),
    ("[URL]", re.compile(r"\b(?:linkedin|github|gitlab)\.com/\S*", re.IGNORECASE)),
    # A bare LinkedIn path, as PDF headers extract "Linkedin: /in/handle/".
    ("[URL]", re.compile(r"(?<!\S)/in/[\w-]+/?")),
    # US numbers: bare 10 digits (common in PDF headers), (555) 123-4567,
    # dots/dashes/spaces, optional +1. Exactly 3+3+4 digits with nothing glued
    # on either side, so dates ("03/2022"), money and metrics don't match.
    ("[PHONE]", re.compile(r"(?<![\w+])(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")),
]


# Layer 3: section names that end the contact block at the top of a resume.
# A line counts only if it is JUST one of these (any case, optional colon), so
# "Strong skills in Python" doesn't end the header early.
_SECTION_HEADINGS = {
    "summary", "professional summary", "profile", "objective", "about me",
    "skills", "technical skills", "core competencies", "key skills",
    "experience", "work experience", "professional experience", "employment history",
    "education", "projects", "certifications", "publications",
}


def _drop_header_block(text: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().rstrip(":").strip().lower() in _SECTION_HEADINGS:
            return "\n".join(lines[i:])
    # No heading found: keep everything; layers 1–2 still apply.
    return text


def redact_pii(text: str, identity: KnownIdentity) -> str:
    text = _drop_header_block(text)

    # Layer 1: what the login token already tells us.
    text = re.sub(re.escape(identity.email), "[EMAIL]", text, flags=re.IGNORECASE)
    # Any whitespace between name parts: PDFs can wrap a name across lines.
    name = r"\s+".join(re.escape(part) for part in identity.name.split())
    text = re.sub(name, "[NAME]", text, flags=re.IGNORECASE)

    for token, pattern in _PATTERNS:
        text = pattern.sub(token, text)
    return text
