"""Seniority level of a job posting — one LLM call per job, shared by every user
(productization plan §3.3). Each user's Seniority Fit is looked up from it in
analysis/seniority_fit.py.

Derived from judge/seniority_fit.py's calibrated rubric, which scores fit for
one entry-level candidate. This prompt drops the candidate entirely and returns
the job's level on five levels (decided 2026-09-16), plus a separate non-fit
reason for agency and contract postings. judge/seniority_fit.py stays as it is
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

### Non-fit postings
Set "non_fit_reason" when one applies; otherwise null. Still give the level
if the posting shows one.
- `agency`: posted by a staffing/recruiting agency or contract-placement firm
  rather than the actual hiring company (signals: "our client", fixed contract
  duration like "12 Months", "W2 only"/"C2C", generic placement-firm branding,
  no real product or team description).
- `contract`: the role itself is explicitly contract/temporary/fixed-term
  (not full-time), even at a direct employer with a real product/team
  description. Contract-to-hire counts.
An internship is not a non-fit posting: it is the level `intern`.

### Rules (apply in order)
1. An internship or co-op → `intern`.
2. Stated years win over title. Use the half-open bands.
3. "X+ years" → use X. A range ("3–7 years") → use the minimum.
4. No years stated → infer the level from the RESPONSIBILITIES described,
   using the examples below as reference points. Set "inferred": true.
5. If title and responsibilities disagree, trust responsibilities and note
   the conflict in "note".
6. Managing engineers → at least `senior`. Managing managers → `staff_principal`.
7. Nothing inferable → "level": null, "confidence": "low".

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
   "note": null, "non_fit_reason": null, "level": "mid_senior"}

Posting: "Senior Machine Learning Engineer. You have 3+ years of
experience building production ML systems."
→ Title says Senior, stated years = 3. Rule 2: years win.
→ {"criterion": "seniority_level",
   "evidence": "Senior Machine Learning Engineer | 3+ years of experience",
   "years_required": 3, "inferred": false, "confidence": "high",
   "note": "title inflated relative to stated years", "non_fit_reason": null, "level": "mid_senior"}

Posting: "Founding AI Engineer at a seed-stage startup. You'll build
our LLM product from scratch, wear many hats, and ship fast. No
specific experience requirement, but you've built real systems."
→ No years. Independent end-to-end ownership, no leadership → mid_senior.
→ {"criterion": "seniority_level",
   "evidence": "Founding AI Engineer | build our LLM product from scratch | built real systems",
   "years_required": null, "inferred": true, "confidence": "medium",
   "note": null, "non_fit_reason": null, "level": "mid_senior"}

Posting: "Machine Learning Engineer. You will define the technical
vision for ML across the organization, mentor senior engineers, and
partner with VPs on strategy."
→ Title sounds mid; duties are org-wide vision + mentoring seniors.
   Rule 5: responsibilities win → staff_principal.
→ {"criterion": "seniority_level",
   "evidence": "define the technical vision | mentor senior engineers | partner with VPs",
   "years_required": null, "inferred": true, "confidence": "high",
   "note": "title understates actual level", "non_fit_reason": null, "level": "staff_principal"}

Posting: "Lead Data Scientist. 7+ years of experience. Mentor a team of
four data scientists and set the modeling roadmap."
→ Years = 7 → band [5, 9) → senior. Mentoring and roadmap fit senior.
→ {"criterion": "seniority_level",
   "evidence": "Lead Data Scientist | 7+ years of experience | set the modeling roadmap",
   "years_required": 7, "inferred": false, "confidence": "high",
   "note": null, "non_fit_reason": null, "level": "senior"}

Posting: "2027 New Graduate Program — Machine Learning. Open to
candidates graduating between Dec 2026 and Jun 2027."
→ Entry-level program by design, regardless of duties described.
→ {"criterion": "seniority_level",
   "evidence": "2027 New Graduate Program | graduating between Dec 2026 and Jun 2027",
   "years_required": null, "inferred": false, "confidence": "high",
   "note": null, "non_fit_reason": null, "level": "entry"}

Posting: "Machine Learning Intern (Summer 2027). Build evaluation tooling
for our LLM features alongside senior engineers."
→ Rule 1: an internship → intern.
→ {"criterion": "seniority_level",
   "evidence": "Machine Learning Intern (Summer 2027)",
   "years_required": null, "inferred": false, "confidence": "high",
   "note": null, "non_fit_reason": null, "level": "intern"}

Posting: "Title: Data Scientist. Duration: 12 Months. *** W2 - USC or GC
only ***. Top skills required: Python or R, time series forecasting,
Marketing Mix Modeling, SQL, Snowflake."
→ Placement-firm signals (fixed contract duration, W2 language, bare skills
   list with no product/team description) → agency. No level is shown.
→ {"criterion": "seniority_level",
   "evidence": "Duration: 12 Months | W2 - USC or GC only | Top skills required",
   "years_required": null, "inferred": true, "confidence": "high",
   "note": "staffing/contract-placement posting, not a direct employer JD", "non_fit_reason": "agency", "level": null}

Posting: "Senior ML Engineer (6-Month Contract) at [product company].
Join our team to own model deployment for our recommendation platform.
5+ years experience preferred. Possibility of extension."
→ Real employer, real product/team description, so not an agency. But it is
   explicitly a fixed-term contract → contract. Years = 5 → senior.
→ {"criterion": "seniority_level",
   "evidence": "Senior ML Engineer (6-Month Contract) | 5+ years experience preferred",
   "years_required": 5, "inferred": false, "confidence": "high",
   "note": "fixed-term contract at a direct employer", "non_fit_reason": "contract", "level": "senior"}

### Output format (JSON, fields in this exact order)
{"criterion": "seniority_level",
 "evidence": "<up to 3 fragments, each <10 words, ' | ' separated>",
 "years_required": <number or null>,
 "inferred": <true/false>,
 "confidence": "high" | "medium" | "low",
 "note": "<conflicts or ambiguities, or null>",
 "non_fit_reason": "agency" | "contract" | null,
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
    non_fit_reason: Literal["agency", "contract"] | None
    level: Literal["intern", "entry", "mid_senior", "senior", "staff_principal"] | None

    @field_validator("non_fit_reason", "level", mode="before")
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
    structured_llm = llm or ChatDeepSeek(
        model=MODEL, extra_body={"thinking": {"type": "disabled"}}
    ).with_structured_output(JobSeniorityLevel)
    return invoke_with_retry(
        structured_llm,
        [SystemMessage(SENIORITY_LEVEL_PROMPT), HumanMessage(posting_text)],
        label="JobSeniorityLevel",
    )
