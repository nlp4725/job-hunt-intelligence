"""
Single entry point for an uploaded resume: file -> normalized text -> skills,
plus the redacted text. See docs/resume_jd_skill_pipeline.md §4.3.
"""

from dataclasses import dataclass

from analysis.skills_extractor import extract_skills
from resume.pii import KnownIdentity, redact_pii
from resume.resume_text import resume_to_text


@dataclass
class ProcessedResume:
    text: str            # normalized, NOT redacted: store encrypted, never log
    redacted_text: str   # the only text allowed into logs, LLM calls, embeddings
    skills: list[str]    # from the full text, the same way JDs are extracted


def process_resume(filename: str, data: bytes, identity: KnownIdentity) -> ProcessedResume:
    """Raises UnsupportedResume (from resume_to_text) for files it can't read."""
    text = resume_to_text(filename, data).text
    return ProcessedResume(
        text=text,
        redacted_text=redact_pii(text, identity),
        skills=extract_skills(text),
    )
