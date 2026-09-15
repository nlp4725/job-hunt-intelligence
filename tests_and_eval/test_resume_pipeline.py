"""process_resume() is the single entry point for an uploaded resume: file ->
normalized text -> skills, plus the redacted text that is the only version
allowed to leave the extractor (logs, LLM calls, embeddings).
See docs/resume_jd_skill_pipeline.md §4.3.
"""

import re
from pathlib import Path

import pytest

from resume.pii import KnownIdentity
from resume.pipeline import process_resume
from tests_and_eval.test_resume_text import _pdf_bytes

JANE = KnownIdentity(name="Jane Doe", email="jane.doe@example.com")

# Real resume PDFs, kept on this machine only (gitignored); skipped on a clone
# without them. Their owners aren't known to the test, so this checks the two
# layers that don't need a login identity: patterns and the header cut.
REAL_PDFS = sorted((Path(__file__).parent / "fixtures" / "skills_gold" / "resumes").glob("*.pdf"))
UNKNOWN_OWNER = KnownIdentity(name="Nobody Known", email="nobody@example.invalid")
CONTACT = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+|linkedin\.com/in/|github\.com/\w|(?<![\d+])\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
)


# Numbered ids, so real names don't appear in test output.
@pytest.mark.parametrize("path", REAL_PDFS, ids=[f"pdf-{i:02d}" for i in range(1, len(REAL_PDFS) + 1)])
class TestRealResumes:
    def test_no_contact_details_survive_and_skills_are_found(self, path):
        result = process_resume(path.name, path.read_bytes(), UNKNOWN_OWNER)
        assert not CONTACT.findall(result.redacted_text)
        assert result.skills


RESUME = (
    "Jane Doe\n"
    "555-123-4567 | jane.doe@example.com | linkedin.com/in/janedoe\n"
    "SUMMARY\n"
    "ML engineer building LLM products.\n"
    "SKILLS\n"
    "Python, SQL, PyTorch, Docker, Prompt Engineering, Vector Database\n"
    "EXPERIENCE\n"
    "Built RAG systems with LangGraph on AWS.\n"
)


class TestProcessResume:
    def test_pdf_resume_gives_skills_and_text_without_contact_details(self):
        result = process_resume("resume.pdf", _pdf_bytes(RESUME), JANE)

        assert {"Python", "SQL", "PyTorch", "Docker", "Prompt Engineering",
                "Vector Database", "RAG", "LangGraph", "AWS", "LLM"} <= set(result.skills)
        for leaked in ("Jane", "Doe", "555-123-4567", "jane.doe@example.com", "janedoe"):
            assert leaked not in result.redacted_text, leaked
        # The test PDF wraps lines, so compare with whitespace collapsed.
        assert "Built RAG systems with LangGraph on AWS." in " ".join(result.redacted_text.split())
