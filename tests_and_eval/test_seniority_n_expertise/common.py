"""
Combined-call variant: scores seniority_fit AND expertise_match in a single
LLM call against a single job posting, reusing the exact same rubric prompts
and output schemas as tests_and_eval/test_seniority/common.py and
tests_and_eval/test_expertise/common.py unchanged — only the wrapper prompt
and the nested output schema are new here. Exists to check whether combining
(cheaper, one call instead of two) costs any accuracy versus scoring each
rubric in its own dedicated call, the way a single flat tool call can
combine multiple rubric dimensions into one call (see the flat-schema
variant below).
"""

from typing import Literal

from pydantic import BaseModel, Field

from tests_and_eval.test_expertise.common import EXPERTISE_MATCH_PROMPT, ExpertiseMatch, get_job_posting
from tests_and_eval.test_seniority.common import SENIORITY_PROMPT, SeniorityFit

# Same job used as test_seniority's/test_expertise's own default smoke-test case.
JOB_ID = 188

COMBINED_PROMPT = f"""You are scoring TWO independent rubrics against the same job posting: \
seniority_fit and expertise_match. Score each one using ONLY that rubric's own rules below — \
do not let one rubric's reasoning, evidence, or score influence the other. Fill in both \
"seniority_fit" and "expertise_match" in the structured output.

{SENIORITY_PROMPT}

{EXPERTISE_MATCH_PROMPT}
"""


class CombinedFit(BaseModel):
    """Nested schema — one sub-object per rubric, each identical in shape to
    the standalone SeniorityFit/ExpertiseMatch models used by the
    single-criterion eval harnesses, so a downstream reader can compare
    field-for-field against those."""

    seniority_fit: SeniorityFit = Field(description="Score per the SENIORITY FIT (0-5) rubric.")
    expertise_match: ExpertiseMatch = Field(description="Score per the EXPERTISE MATCH (0-5) rubric.")


class CombinedFitFlat(BaseModel):
    """Flat mirror of CombinedFit — every field at the top level, fields
    prefixed by rubric instead of grouped into sub-objects. Mirrors
    judge_agent.py's submit_rubric tool, which combines 5 rubric dimensions
    the same flat way with no reported issue. Exists to test whether
    CombinedFit's ~10-13% parse-failure rate (the model splitting one tool
    call into two, one per sub-object, each missing the other's required
    field — see run_eval.py) was caused by the nesting itself rather than
    genuine cross-rubric interference."""

    seniority_evidence: str = Field(
        description="Up to 3 fragments quoted from the posting, each under 10 words, ' | ' separated. Seniority evidence only."
    )
    seniority_years_required: int | None = Field(description="Minimum years of experience stated in the posting, or null.")
    seniority_inferred: bool = Field(description="True if seniority_years_required is null and the score was inferred from responsibilities.")
    seniority_confidence: Literal["high", "medium", "low"]
    seniority_note: str | None = Field(description="Conflicts or ambiguities in the seniority rubric, or null.")
    seniority_score: int = Field(ge=0, le=5, description="Score per the SENIORITY FIT (0-5) rubric.")

    expertise_evidence: str = Field(
        description="Up to 3 fragments quoted from the posting, each under 10 words, ' | ' separated. Expertise evidence only."
    )
    expertise_matched_domains: list[Literal["D1", "D2", "D3"]] = Field(description="Domains this role's core problem matches, or empty.")
    expertise_matched_capabilities: list[Literal["C1", "C2", "C3", "C4", "C5", "C6"]] = Field(
        description="Capabilities this role's core problem matches, or empty."
    )
    expertise_matched_weaknesses: list[Literal["W1", "W2", "W3"]] = Field(
        description="Weaknesses that are a core requirement of this role, or empty."
    )
    expertise_confidence: Literal["high", "medium", "low"]
    expertise_note: str | None = Field(description="Nuances in the expertise rubric, or null.")
    expertise_score: int = Field(ge=0, le=5, description="Score per the EXPERTISE MATCH (0-5) rubric.")


__all__ = ["COMBINED_PROMPT", "CombinedFit", "CombinedFitFlat", "JOB_ID", "get_job_posting"]
