"""process_resume() is the single entry point for an uploaded resume: file ->
normalized text -> skills, plus the redacted text that is the only version
allowed to leave the extractor (logs, LLM calls, embeddings).
See docs/resume_jd_skill_pipeline.md §4.3.
"""

from resume.pii import KnownIdentity
from resume.pipeline import process_resume
from tests_and_eval.test_resume_text import _pdf_bytes

JANE = KnownIdentity(name="Jane Doe", email="jane.doe@example.com")

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
