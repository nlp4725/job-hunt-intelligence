"""Seniority level of a job posting — one LLM call per job, shared by every user
(productization plan §3.3). Each user's Seniority Fit is looked up from it in
analysis/seniority_fit.py.

Derived from judge/seniority_fit.py's calibrated rubric, which scores fit for
one entry-level candidate. This prompt drops the candidate entirely and returns
the job's level on five levels (decided 2026-09-16), plus a separate
yes/no attribute is_contract. Agency postings are filtered out before this step
(judge/agency_blocklist.py). judge/seniority_fit.py stays as it is
for the local app.
"""

import math
from typing import Literal

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from pydantic import BaseModel, Field, field_validator

from judge.structured_retry import invoke_with_retry

load_dotenv()

MODEL = "deepseek-v4-pro"
MAX_EMPTY_ANSWERS = 3

SENIORITY_LEVEL_PROMPT = """## SENIORITY LEVEL
Classify the level of responsibility a job posting describes. This is a
property of the job alone, not of any candidate. Judge by the LEVEL OF
RESPONSIBILITY the posting describes, not by job title strings.

| Level | Years (half-open) | What the role actually expects |
|-------|-------------------|--------------------------------|
| `intern` | — | An internship or co-op, whatever the duties. |
| `entry` | [0, 2) | New grad / entry. Executes well-defined tasks under supervision. Posting caps experience or targets recent graduates. |
| `mid_senior` | [2, 5) | Works independently, owns features end-to-end, collaborates across teams. Expected to ship, not to lead. |
| `senior` | [5, 9) | Owns whole projects and makes technical decisions; may mentor, lead a team's technical direction, or manage engineers. |
| `staff_principal` | [9, ∞) | Staff / principal / director. Drives architecture across multiple teams or the org, sets strategy, or manages managers. |

### One yes/no attribute
Agency and recruiter postings are filtered out before this step, so judge the
role as posted. Every posting gets "is_contract", true or false, decided
independently of the level. The level is still decided exactly like any other
posting; use a null level only when nothing about seniority can be inferred.
- "is_contract": true when the role itself is contract / temporary /
  fixed-term / hourly or part-time freelance (not a full-time permanent job).
  Contract-to-hire counts. Boilerplate like "employees and contractors" or
  hourly pay for a full-time job does not make it a contract.

### How to decide the level (in order)
1. An internship or co-op → `intern`.
2. Find the minimum OVERALL years of experience the posting REQUIRES:
   - "X+ years" → X. A range ("3–7 years") → the minimum.
   - Different paths ("BS + 5 or MS + 3") → the lowest path.
   - Ignore years that are only "preferred" / "nice to have".
   - Years in one specific tool or skill don't count when an overall figure
     is given; when several overall figures are required together, use the
     largest.
3. If required years were found, the level is their band. Title words
   (Senior, Lead, Staff, Principal, Director) and duties do NOT move a level
   that stated years set, even when the duties look modest or hands-on for
   that many years; mention a conflict in "note".
4. No required years → infer the level from the RESPONSIBILITIES, using the
   examples below. Set "inferred": true. A title alone is a weak hint, but an
   explicit Staff / Principal / Director level for the role ("As a Staff
   Engineer…") backed by cross-team or org-wide duties → `staff_principal`.
5. People management sets a floor in every case, even over stated years:
   managing people (engineers, scientists, analysts) → at least `senior`;
   managing managers → `staff_principal`.
6. Nothing inferable → "level": null, "confidence": "low".

### Evidence format
"evidence" = up to 3 short fragments quoted from the posting, each UNDER 10
WORDS, separated by " | ". Quote only the words carrying the signal (years,
title, key duty). Never full sentences.

### Examples

Posting: "ML Engineer — 3+ years experience. You'll own our ranking
model pipeline from experimentation to deployment, working closely
with product and data teams."
→ {"criterion": "seniority_level",
   "evidence": "ML Engineer | 3+ years experience | own our ranking model pipeline",
   "years_required": 3, "inferred": false, "confidence": "high",
   "note": null, "is_contract": false, "level": "mid_senior"}

Posting: "Senior Machine Learning Engineer. You have 3+ years of
experience building production ML systems."
→ Title says Senior, stated years = 3. Rule 3: years set the level.
→ {"criterion": "seniority_level",
   "evidence": "Senior Machine Learning Engineer | 3+ years of experience",
   "years_required": 3, "inferred": false, "confidence": "high",
   "note": "title inflated relative to stated years", "is_contract": false, "level": "mid_senior"}

Posting: "Founding AI Engineer at a seed-stage startup. You'll build
our LLM product from scratch, wear many hats, and ship fast. No
specific experience requirement, but you've built real systems."
→ No years. Independent end-to-end ownership, no leadership → mid_senior.
→ {"criterion": "seniority_level",
   "evidence": "Founding AI Engineer | build our LLM product from scratch | built real systems",
   "years_required": null, "inferred": true, "confidence": "medium",
   "note": null, "is_contract": false, "level": "mid_senior"}

Posting: "Machine Learning Engineer. You will define the technical
vision for ML across the organization, mentor senior engineers, and
partner with VPs on strategy."
→ No years. Title sounds mid; duties are org-wide vision + mentoring
   seniors. Rule 4: responsibilities decide → staff_principal.
→ {"criterion": "seniority_level",
   "evidence": "define the technical vision | mentor senior engineers | partner with VPs",
   "years_required": null, "inferred": true, "confidence": "high",
   "note": "title understates actual level", "is_contract": false, "level": "staff_principal"}

Posting: "Senior Systems Engineer at a 20-person IT services company. 10+ years
of IT experience required. Resolve escalated client tickets and mentor two
junior technicians."
→ Rule 3: required years = 10 → staff_principal, even though the duties are
   hands-on and the team is small.
→ {"criterion": "seniority_level",
   "evidence": "Senior Systems Engineer | 10+ years of IT experience required",
   "years_required": 10, "inferred": false, "confidence": "high",
   "note": "duties modest for 10 years; stated years set the level", "is_contract": false, "level": "staff_principal"}

Posting: "Senior GenAI Engineer. Build plugins and integrations with business
teams; hands-on development and testing. No experience requirement stated."
→ No years. The Senior title is only a hint; the duties are independent,
   hands-on building with no project leadership or mentoring → mid_senior.
→ {"criterion": "seniority_level",
   "evidence": "Senior GenAI Engineer | Build plugins and integrations | hands-on development and testing",
   "years_required": null, "inferred": true, "confidence": "medium",
   "note": "title says Senior; duties are mid-level", "is_contract": false, "level": "mid_senior"}

Posting: "Lead Data Scientist. 7+ years of experience. Mentor a team of
four data scientists and set the modeling roadmap."
→ Years = 7 → band [5, 9) → senior. Mentoring and roadmap fit senior.
→ {"criterion": "seniority_level",
   "evidence": "Lead Data Scientist | 7+ years of experience | set the modeling roadmap",
   "years_required": 7, "inferred": false, "confidence": "high",
   "note": null, "is_contract": false, "level": "senior"}

Posting: "2027 New Graduate Program — Machine Learning. Open to
candidates graduating between Dec 2026 and Jun 2027."
→ Entry-level program by design, regardless of duties described.
→ {"criterion": "seniority_level",
   "evidence": "2027 New Graduate Program | graduating between Dec 2026 and Jun 2027",
   "years_required": null, "inferred": false, "confidence": "high",
   "note": null, "is_contract": false, "level": "entry"}

Posting: "Machine Learning Intern (Summer 2027). Build evaluation tooling
for our LLM features alongside senior engineers."
→ Rule 1: an internship → intern.
→ {"criterion": "seniority_level",
   "evidence": "Machine Learning Intern (Summer 2027)",
   "years_required": null, "inferred": false, "confidence": "high",
   "note": null, "is_contract": false, "level": "intern"}

Posting: "Staff Machine Learning Engineer. 7+ years of experience building
ML systems. Drive architecture for our ranking platform."
→ Rule 3: required years = 7 → senior. The Staff title does not move it.
→ {"criterion": "seniority_level",
   "evidence": "Staff Machine Learning Engineer | 7+ years of experience",
   "years_required": 7, "inferred": false, "confidence": "high",
   "note": "title says Staff; stated years set senior", "is_contract": false, "level": "senior"}

Posting: "Senior ML Engineer (6-Month Contract) at [product company].
Join our team to own model deployment for our recommendation platform.
5+ years experience preferred. Possibility of extension."
→ Explicitly a fixed-term contract → is_contract. Years = 5 → senior.
→ {"criterion": "seniority_level",
   "evidence": "Senior ML Engineer (6-Month Contract) | 5+ years experience preferred",
   "years_required": 5, "inferred": false, "confidence": "high",
   "note": "fixed-term contract at a direct employer", "is_contract": true, "level": "senior"}

### Output format (JSON, fields in this exact order)
{"criterion": "seniority_level",
 "evidence": "<up to 3 fragments, each <10 words, ' | ' separated>",
 "years_required": <number or null>,
 "inferred": <true/false>,
 "confidence": "high" | "medium" | "low",
 "note": "<conflicts or ambiguities, or null>",
 "is_contract": true | false,
 "level": "intern" | "entry" | "mid_senior" | "senior" | "staff_principal" | null}

Respond with ONLY that JSON object — no surrounding prose, no markdown code fence.
"""


class JobSeniorityLevel(BaseModel):
    """Field order mirrors the prompt's output format."""

    criterion: Literal["seniority_level"] = "seniority_level"
    evidence: str = Field(description="Up to 3 fragments quoted from the posting, each under 10 words, ' | ' separated.")
    years_required: int | None = Field(description="Minimum years of experience stated in the posting, or null.")
    inferred: bool = Field(description="True if years_required is null and the level was inferred from responsibilities.")
    confidence: Literal["high", "medium", "low"]
    note: str | None = Field(description="Conflicts or ambiguities, or null.")
    is_contract: bool = Field(description="Contract / temporary / fixed-term / part-time freelance role.")
    level: Literal["intern", "entry", "mid_senior", "senior", "staff_principal"] | None

    @field_validator("level", mode="before")
    @classmethod
    def _null_text_is_null(cls, v):
        # DeepSeek sometimes writes JSON null as the string "null" (first parity
        # run, 2026-09-16); every retry repeated it, so the call failed outright.
        return None if isinstance(v, str) and v.strip().lower() in ("null", "none", "") else v

    @field_validator("years_required", mode="before")
    @classmethod
    def _round_up_fractional_years(cls, v):
        # "1.5+ years" means the floor is past 1 whole year (same as judge/seniority_fit.py).
        return math.ceil(v) if isinstance(v, float) else v


def classify_job_seniority(posting_text: str, llm=None) -> JobSeniorityLevel:
    """`posting_text` as built by judge.seniority_fit.format_posting. `llm` is a
    with_structured_output runnable; defaults to DeepSeek with thinking off."""
    # temperature 0: a job's level is a stored fact shared by every user, so the
    # same posting should get the same answer; sampling made 3 runs of one
    # posting disagree on ~8% of training jobs (2026-09-16).
    structured_llm = llm or ChatDeepSeek(
        model=MODEL, temperature=0, extra_body={"thinking": {"type": "disabled"}}
    ).with_structured_output(JobSeniorityLevel)
    messages = [SystemMessage(SENIORITY_LEVEL_PROMPT), HumanMessage(posting_text)]
    # The structured-output parser returns None (instead of raising) when the
    # model's answer can't be parsed at all; seen twice in 147 calls on
    # 2026-09-16. Retry those the same way as a failed validation.
    for _ in range(MAX_EMPTY_ANSWERS):
        result = invoke_with_retry(structured_llm, messages, label="JobSeniorityLevel")
        if result is not None:
            return result
    raise ValueError(f"no parseable answer after {MAX_EMPTY_ANSWERS} attempts")
