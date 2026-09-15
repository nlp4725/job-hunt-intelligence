"""Unit tests for analysis/jd_sections.py — the JD section splitter.

Every case here is anchored to a real posting in the corpus that the splitter
got wrong at some point during development; the docstrings name the job id so
a future reader can go look at the source text.
"""

import pytest

from analysis.jd_sections import (
    blurb_text,
    requirement_text,
    split_sections,
)


class TestBlurbIsolation:
    """Company marketing must never be read as a job requirement."""

    def test_agentic_in_about_us_stays_out_of_requirements(self):
        """Job 10360 (Sigma, "Data Engineer, Data Platform").

        Its About Us says "the AI Apps and agentic analytics platform", so a
        whole-blob extractor tags the posting with `Agents` — while the actual
        job is Snowflake/dbt/Terraform pipelines with no agent work at all.
        """
        jd = (
            "About the job Data Engineer, Data Platform "
            "About The Role You will own ETL pipelines across Snowflake and Databricks. "
            "Qualifications 3+ years in a data engineering role. Familiarity with dbt. "
            "About Us Sigma is the AI Apps and agentic analytics platform built on the "
            "cloud data warehouse."
        )
        body, blurb = requirement_text(jd), blurb_text(jd)
        assert "agentic" not in body.lower()
        assert "agentic" in blurb.lower()
        assert "snowflake" in body.lower()

    def test_benefits_are_not_requirements(self):
        jd = (
            "About the job Responsibilities Build LLM pipelines. "
            "Benefits Unlimited PTO, dental, 401(k) with company match."
        )
        assert "dental" not in requirement_text(jd).lower()
        assert "dental" in blurb_text(jd).lower()


class TestBodyRecovery:
    """The real job text must never be stranded in the blurb bucket."""

    def test_stray_skills_word_in_compensation_does_not_strand_body(self):
        """Job 12734 (Machinify, "Staff AI Engineer").

        The posting has no responsibilities/qualifications headings — only
        salary and benefits ones. The word "skills" inside a compensation
        sentence ("...skills, certifications, etc.") looks like a requirements
        heading, which used to leave the entire 2.8k-char body filed as
        company blurb and the job scored as marketing-only.
        """
        body_text = (
            "About the jobMachinify builds healthcare intelligence. "
            + "You will build RAG systems, agent orchestration and LLM evals. " * 30
        )
        jd = (
            body_text
            + "The base salary for this position is based on an array of factors unique "
            "to each candidate: such skills, certifications, etc. We are hiring for "
            "different levels and the base salary can range from $210k-$280k."
        )
        body = requirement_text(jd)
        assert "RAG systems" in body
        assert len(body) > 300

    def test_posting_with_no_headings_is_all_body(self):
        jd = "We need someone to build LLM agents and ship them to production."
        assert requirement_text(jd) == jd
        assert blurb_text(jd) == ""

    def test_lead_text_is_body_when_no_real_sections_follow(self):
        """LinkedIn always prefixes "About the job", so leading text is the
        posting itself — not a company blurb."""
        jd = "About the job We need an engineer to build RAG pipelines. Benefits: PTO."
        assert "RAG pipelines" in requirement_text(jd)

    def test_lead_text_becomes_blurb_when_a_real_body_exists(self):
        jd = (
            "About the job Acme is an agentic analytics company. "
            "Responsibilities " + "Build and maintain ETL pipelines in Snowflake. " * 12
        )
        assert "agentic" in blurb_text(jd).lower()
        assert "agentic" not in requirement_text(jd).lower()


class TestChromeStripping:
    """LinkedIn page chrome leaks into scraped raw_text and must be removed."""

    def test_premium_upsell_carrying_user_name_is_stripped(self):
        """Present in 11.4% of scraped ml_ai JDs. Carries the logged-in user's
        own first name, so it is matched generically rather than by name."""
        jd = (
            "About the job Responsibilities Build agents. "
            "Premium subscribers are 2.7x more likely to get hired on average "
            "Randy and millions of other members use LinkedIn to find jobs."
        )
        whole = requirement_text(jd) + blurb_text(jd)
        assert "Randy" not in whole
        assert "millions of other members" not in whole
        assert "Build agents" in requirement_text(jd)

    def test_inline_page_javascript_is_stripped(self):
        jd = (
            "About the job Responsibilities Ship LLM features. "
            "window.__missing_como_module_list__.forEach((id) => { "
            "Promise.reject(new Error('Missing como module: ' + id)); });"
        )
        whole = requirement_text(jd) + blurb_text(jd)
        assert "Promise.reject" not in whole
        assert "Ship LLM features" in requirement_text(jd)


class TestPartition:
    def test_sections_cover_every_kind_returned(self):
        jd = (
            "About the job Responsibilities Build things. "
            "Qualifications 5 years. About Us We are a company. Benefits PTO."
        )
        kinds = {k for k, _ in split_sections(jd)}
        assert kinds <= {"role", "req", "co", "pay"}

    def test_empty_input_returns_empty(self):
        assert split_sections("") == []
        assert requirement_text("") == ""
        assert blurb_text("") == ""

    def test_none_safe(self):
        assert split_sections(None) == []
