"""
Shared rubric prompt, output schema, and job-fetch helper for the
seniority_fit model comparison. test_seniority_claude.py and
test_seniority_deepseek.py both import from here so they score the exact
same prompt against the exact same job — any difference in their output is
attributable to the model, not to prompt drift between the two files.
"""

from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from db.models import Job
from db.session import get_session

load_dotenv()

# Real job pulled from data/job_hunt.db: "Senior AI Engineer" @ Tiger
# Analytics, with an explicit "5+ years of experience" requirement — a clean
# case (title and stated years agree) to check whether both models land on
# the same band from the same evidence before trying ambiguous postings.
JOB_ID = 188

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
| 0 | Principal / Director / Intern | [12, ∞) or internship | Org-wide technical strategy, manages managers — OR an internship. Hard non-fit either way. |

### Scoring rules (apply in order)
1. If the posting is from a staffing/recruiting agency or contract-placement
   firm rather than the actual hiring company (signals: fixed contract
   duration like "12 Months," "W2 only"/"C2C," generic placement-firm
   branding, no real product or team description) → score 0 regardless of
   years or responsibilities stated. Explain why in "note" and stop —
   skip the remaining rules.
2. Stated years win over title. Use the half-open bands.
3. "X+ years" → use X. A range ("3–7 years") → use the minimum.
4. No years stated → infer the level from the RESPONSIBILITIES
   described, using the examples below as reference points.
   Set "inferred": true.
5. If title and responsibilities disagree, trust responsibilities
   and note the conflict in "note".
6. Nothing inferable → score 3, "confidence": "low".

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


def get_job_posting(job_id: int = JOB_ID) -> str:
    session = get_session()
    try:
        job = session.get(Job, job_id)
        return f'Posting: "{job.title} at {job.company_name}"\n\n{job.raw_text}'
    finally:
        session.close()
