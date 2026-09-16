"""Seniority level of a job posting — one LLM call per job, shared by every user
(productization plan §3.3). Each user's Seniority Fit is computed from it in
analysis/seniority_fit.py.

Derived from judge/seniority_fit.py's calibrated rubric, which scores fit for
one entry-level candidate. This prompt drops the candidate entirely and
returns the level itself, and splits that rubric's score 0 — which mixed
principal roles with agency, contract and internship postings — into a level
plus a separate non-fit reason. judge/seniority_fit.py stays as it is for the
local app.
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
| `entry` | [0, 2) | New Grad / Entry. Executes well-defined tasks under supervision. Posting caps experience or targets recent graduates. |
| `mid` | [2, 5) | Works independently, owns features end-to-end, collaborates across teams. Expected to ship, not to lead. |
| `senior` | [5, 7) | Owns whole projects, makes technical decisions. Mentorship is a plus, not a duty. |
| `senior_plus` | [7, 9) | Mentoring and cross-team technical leadership are core responsibilities. Sets technical direction for a team. |
| `staff` | [9, 12) | Staff / Lead. Drives architecture across multiple teams. Influences roadmap. Deep specialization assumed. |
| `principal` | [12, ∞) | Principal / Director. Org-wide technical strategy, manages managers. |

### Non-fit postings
Set "non_fit_reason" when one applies; otherwise null. Still give the level
if the posting shows one.
- `agency`: posted by a staffing/recruiting agency or contract-placement firm
  rather than the actual hiring company (signals: fixed contract duration like
  "12 Months", "W2 only"/"C2C", generic placement-firm branding, no real
  product or team description).
- `contract`: the role itself is explicitly contract/temporary/fixed-term
  (not full-time), even at a direct employer with a real product/team
  description.
- `internship`: an internship.

### Rules (apply in order)
1. Stated years win over title. Use the half-open bands.
2. "X+ years" → use X. A range ("3–7 years") → use the minimum.
3. No years stated → infer the level from the RESPONSIBILITIES described,
   using the examples below as reference points. Set "inferred": true.
4. If title and responsibilities disagree, trust responsibilities and note
   the conflict in "note".
5. Nothing inferable → "level": null, "confidence": "low".

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
   "note": null, "non_fit_reason": null, "level": "mid"}

Posting: "Senior Machine Learning Engineer. You have 3+ years of
experience building production ML systems."
→ Title says Senior, stated years = 3. Rule 1: years win.
→ {"criterion": "seniority_level",
   "evidence": "Senior Machine Learning Engineer | 3+ years of experience",
   "years_required": 3, "inferred": false, "confidence": "high",
   "note": "title inflated relative to stated years", "non_fit_reason": null, "level": "mid"}

Posting: "Founding AI Engineer at a seed-stage startup. You'll build
our LLM product from scratch, wear many hats, and ship fast. No
specific experience requirement, but you've built real systems."
→ No years. Independent end-to-end ownership, no leadership → mid.
→ {"criterion": "seniority_level",
   "evidence": "Founding AI Engineer | build our LLM product from scratch | built real systems",
   "years_required": null, "inferred": true, "confidence": "medium",
   "note": null, "non_fit_reason": null, "level": "mid"}

Posting: "ML Engineer II at [large tech co]. Collaborate with
scientists to productionize models; participate in design reviews."
→ No years. "Engineer II" + independent execution, no leadership → mid.
→ {"criterion": "seniority_level",
   "evidence": "ML Engineer II | productionize models | participate in design reviews",
   "years_required": null, "inferred": true, "confidence": "high",
   "note": null, "non_fit_reason": null, "level": "mid"}

Posting: "Machine Learning Engineer. You will define the technical
vision for ML across the organization, mentor senior engineers, and
partner with VPs on strategy."
→ Title sounds mid; duties are org-wide vision + mentoring seniors.
   Rule 4: responsibilities win → principal.
→ {"criterion": "seniority_level",
   "evidence": "define the technical vision | mentor senior engineers | partner with VPs",
   "years_required": null, "inferred": true, "confidence": "high",
   "note": "title understates actual level", "non_fit_reason": null, "level": "principal"}

Posting: "2027 New Graduate Program — Machine Learning. Open to
candidates graduating between Dec 2026 and Jun 2027."
→ Entry-level program by design, regardless of duties described.
→ {"criterion": "seniority_level",
   "evidence": "2027 New Graduate Program | graduating between Dec 2026 and Jun 2027",
   "years_required": null, "inferred": false, "confidence": "high",
   "note": null, "non_fit_reason": null, "level": "entry"}

Posting: "Applied Scientist — recommendation systems. PhD required
or MS with 4+ years. You'll lead projects and mentor junior scientists;
mentoring is encouraged but not required for promotion."
→ years=4 → band [2,5), top edge; senior-flavored optional duties.
   Years govern → mid, flag the ambiguity.
→ {"criterion": "seniority_level",
   "evidence": "MS with 4+ years | lead projects | mentoring is encouraged",
   "years_required": 4, "inferred": false, "confidence": "medium",
   "note": "4 yrs = top of mid band; senior-flavored duties", "non_fit_reason": null, "level": "mid"}

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
 "non_fit_reason": "agency" | "contract" | "internship" | null,
 "level": "entry" | "mid" | "senior" | "senior_plus" | "staff" | "principal" | null}

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
    non_fit_reason: Literal["agency", "contract", "internship"] | None
    level: Literal["entry", "mid", "senior", "senior_plus", "staff", "principal"] | None

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
