"""
Seniority Fit — one of the three Stage 1 screening signals (see
judge/stage1_screen.py, CONTEXT.md "Job Judge"). Canonical home for the
calibrated prompt/schema/scorer; tests_and_eval/test_seniority/common.py
imports from here rather than defining its own copy, so the eval harness
and production always score the exact same prompt.

Model choice (DeepSeek V4 Pro, thinking disabled) is the "Stage 1 model
choice" recorded in CONTEXT.md — chosen over Claude Haiku 4.5 for being
cheaper AND more accurate on this rubric specifically (MAE 0.317 vs 0.333),
per tests_and_eval/test_seniority/run_eval.py.
"""

from typing import Literal

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from pydantic import BaseModel, Field

from db.models import Job

load_dotenv()

MODEL = "deepseek-v4-pro"

SENIORITY_PROMPT = """## SENIORITY FIT (0–5)
Score how well the role's seniority matches my profile (~3 years
hands-on ML/AI experience, targeting mid-level). Judge by the LEVEL
OF RESPONSIBILITY the posting describes, not by job title strings.

| Score | Level | Years (half-open) | What the role actually expects |
|-------|-------|-------------------|--------------------------------|
| 5 | Mid-level | [2, 5) | Works independently, owns features end-to-end, collaborates across teams. Expected to ship, not to lead. |
| 4 | Senior (lower) | [5, 7) | Owns whole projects, makes technical decisions. Mentorship is a plus, not a duty. |
| 3 | Senior (upper) | [7, 9) | Mentoring and cross-team technical leadership are core responsibilities. Sets technical direction for a team. |
| 2 | New Grad / Entry | [0, 2) | Executes well-defined tasks under supervision. Posting caps experience or targets recent graduates. |
| 1 | Staff / Lead | [9, 12) | Drives architecture across multiple teams. Influences roadmap. Deep specialization assumed. |
| 0 | Principal / Director / Intern / Contract | [12, ∞) or internship or contract | Org-wide technical strategy, manages managers — OR an internship — OR an explicitly contract/temporary/fixed-term position (not full-time), even at a direct employer with a real product/team description. Hard non-fit either way, regardless of years or responsibilities stated. |

### Scoring rules (apply in order)
1. If the posting is from a staffing/recruiting agency or contract-placement
   firm rather than the actual hiring company (signals: fixed contract
   duration like "12 Months," "W2 only"/"C2C," generic placement-firm
   branding, no real product or team description) → score 0 regardless of
   years or responsibilities stated. Explain why in "note" and stop —
   skip the remaining rules.
2. If the role itself is explicitly contract/temporary/fixed-term (not
   full-time) — even at a direct employer, with a real product/team
   description, not caught by rule 1 — → score 0 the same as an
   internship, regardless of years or responsibilities stated. Same
   treatment as rule 1, just a different trigger (the employment TYPE
   itself, not the posting SOURCE).
3. Stated years win over title. Use the half-open bands.
4. "X+ years" → use X. A range ("3–7 years") → use the minimum.
5. No years stated → infer the level from the RESPONSIBILITIES
   described, using the examples below as reference points.
   Set "inferred": true.
6. If title and responsibilities disagree, trust responsibilities
   and note the conflict in "note".
7. Nothing inferable → score 3, "confidence": "low".

### Evidence format
"evidence" = up to 3 short fragments quoted from the posting, each
UNDER 10 WORDS, separated by " | ". Quote only the words carrying
the signal (years, title, key duty). Never full sentences.

### Examples

Posting: "ML Engineer — 3+ years experience. You'll own our ranking
model pipeline from experimentation to deployment, working closely
with product and data teams."
→ {"criterion": "seniority_fit",
   "evidence": "ML Engineer | 3+ years experience | own our ranking model pipeline",
   "years_required": 3, "inferred": false, "confidence": "high",
   "note": null, "score": 5}

Posting: "Senior Machine Learning Engineer. You have 3+ years of
experience building production ML systems."
→ Title says Senior, stated years = 3. Rule 1: years win.
→ {"criterion": "seniority_fit",
   "evidence": "Senior Machine Learning Engineer | 3+ years of experience",
   "years_required": 3, "inferred": false, "confidence": "high",
   "note": "title inflated relative to stated years", "score": 5}

Posting: "Founding AI Engineer at a seed-stage startup. You'll build
our LLM product from scratch, wear many hats, and ship fast. No
specific experience requirement, but you've built real systems."
→ No years. Independent end-to-end ownership, no leadership → mid-level.
→ {"criterion": "seniority_fit",
   "evidence": "Founding AI Engineer | build our LLM product from scratch | built real systems",
   "years_required": null, "inferred": true, "confidence": "medium",
   "note": null, "score": 5}

Posting: "ML Engineer II at [large tech co]. Collaborate with
scientists to productionize models; participate in design reviews."
→ No years. "Engineer II" + independent execution, no leadership → mid.
→ {"criterion": "seniority_fit",
   "evidence": "ML Engineer II | productionize models | participate in design reviews",
   "years_required": null, "inferred": true, "confidence": "high",
   "note": null, "score": 5}

Posting: "Machine Learning Engineer. You will define the technical
vision for ML across the organization, mentor senior engineers, and
partner with VPs on strategy."
→ Title sounds mid; duties are org-wide vision + mentoring seniors.
   Rule 4: responsibilities win → Staff/Principal territory.
→ {"criterion": "seniority_fit",
   "evidence": "define the technical vision | mentor senior engineers | partner with VPs",
   "years_required": null, "inferred": true, "confidence": "high",
   "note": "title understates actual level", "score": 0}

Posting: "2027 New Graduate Program — Machine Learning. Open to
candidates graduating between Dec 2026 and Jun 2027."
→ Entry-level program by design, regardless of duties described.
→ {"criterion": "seniority_fit",
   "evidence": "2027 New Graduate Program | graduating between Dec 2026 and Jun 2027",
   "years_required": null, "inferred": false, "confidence": "high",
   "note": null, "score": 2}

Posting: "Applied Scientist — recommendation systems. PhD required
or MS with 4+ years. You'll lead projects and mentor junior scientists;
mentoring is encouraged but not required for promotion."
→ years=4 → band [2,5), top edge; senior-flavored optional duties.
   Years govern → 5, flag the ambiguity.
→ {"criterion": "seniority_fit",
   "evidence": "MS with 4+ years | lead projects | mentoring is encouraged",
   "years_required": 4, "inferred": false, "confidence": "medium",
   "note": "4 yrs = top of mid band; senior-flavored duties", "score": 5}

Posting: "Title: Data Scientist. Duration: 12 Months. *** W2 - USC or GC
only ***. Top skills required: Python or R, time series forecasting,
Marketing Mix Modeling, SQL, Snowflake."
→ Rule 1: staffing/placement-firm signals (fixed contract duration, W2
   language, bare skills list with no product/team description) — score 0,
   skip the rest of the rules regardless of the skills list looking mid-level.
→ {"criterion": "seniority_fit",
   "evidence": "Duration: 12 Months | W2 - USC or GC only | Top skills required",
   "years_required": null, "inferred": false, "confidence": "high",
   "note": "staffing/contract-placement posting, not a direct employer JD", "score": 0}

Posting: "Senior ML Engineer (6-Month Contract) at [product company].
Join our team to own model deployment for our recommendation platform.
5+ years experience preferred. Possibility of extension."
→ Real employer, real product/team description — Rule 1 doesn't apply.
   But it's explicitly a fixed-term contract role. Rule 2: contract → 0,
   regardless of the 5+ years / senior-sounding responsibilities.
→ {"criterion": "seniority_fit",
   "evidence": "Senior ML Engineer (6-Month Contract) | 5+ years experience preferred",
   "years_required": 5, "inferred": false, "confidence": "high",
   "note": "explicitly a fixed-term contract role at a direct employer — contract type governs over years/responsibilities", "score": 0}

### Output format (JSON, fields in this exact order)
{"criterion": "seniority_fit",
 "evidence": "<up to 3 fragments, each <10 words, ' | ' separated>",
 "years_required": <number or null>,
 "inferred": <true/false>,
 "confidence": "high" | "medium" | "low",
 "note": "<conflicts or ambiguities, or null>",
 "score": <0-5>}

Respond with ONLY that JSON object — no surrounding prose, no markdown code fence.
"""


class SeniorityFit(BaseModel):
    """Output shape mirrors the rubric's "Output format (JSON, fields in
    this exact order)" section — field order here is preserved in the tool
    schema handed to the model."""

    criterion: Literal["seniority_fit"] = "seniority_fit"
    evidence: str = Field(
        description="Up to 3 fragments quoted from the posting, each under 10 words, ' | ' separated."
    )
    years_required: int | None = Field(description="Minimum years of experience stated in the posting, or null.")
    inferred: bool = Field(description="True if years_required is null and the score was inferred from responsibilities.")
    confidence: Literal["high", "medium", "low"]
    note: str | None = Field(description="Conflicts or ambiguities (e.g. title vs. years disagreement), or null.")
    score: int = Field(ge=0, le=5)


def format_posting(job: Job) -> str:
    return f'Posting: "{job.title} at {job.company_name}"\n\n{job.raw_text}'


def score_seniority_fit(posting_text: str) -> SeniorityFit:
    llm = ChatDeepSeek(model=MODEL, extra_body={"thinking": {"type": "disabled"}})
    structured_llm = llm.with_structured_output(SeniorityFit)
    return structured_llm.invoke([
        SystemMessage(SENIORITY_PROMPT),
        HumanMessage(posting_text),
    ])
