"""
Shared rubric prompt, output schema, and job-fetch helper for the
expertise_match model comparison — mirrors tests_and_eval/test_seniority/common.py.
test_expertise_claude.py and test_expertise_deepseek.py both import from
here so they score the exact same prompt against the exact same job.
"""

from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from db.models import Job
from db.session import get_session

load_dotenv()

# Real job pulled from data/job_hunt.db: "Sr Machine Learning Scientist" @
# Amgen — pharma domain (D3) but the core job is training foundational /
# diffusion models from scratch (W3), despite the domain match. Good
# smoke-test case since it exercises rule 2 (a W-item overriding a D match),
# not just a trivial full match.
JOB_ID = 5

EXPERTISE_MATCH_PROMPT = """## EXPERTISE MATCH (0–5)
Score how much my background is an advantage for this role's core
problem. Match on TWO axes: DOMAIN (industry) and CAPABILITY
(problem type). Score the role's core problem — what the hire
spends most days doing — not the company's industry.

### My domains (D)
D1. Retail / e-commerce / marketplaces
D2. Marketing & consumer (to-C) products
D3. Pharmaceutical / healthcare / life sciences

### My capabilities (C)
C1. End-to-end ML modeling: data cleaning, target definition,
    feature engineering, model training (classical/traditional ML —
    e.g. XGBoost, random forest, regression, classical time-series
    methods), benchmarking/evaluation against business baselines.
    Problem types incl. prediction, recommenders, forecasting.
C2. Building & deploying ML/AI systems end-to-end (LLM/RAG/agent
    systems, cloud deployment, CI/CD)
C3. Pain-point & market analysis: identifying customer problems,
    sizing opportunities
C4. 0→1 product development: idea → build → launch
C5. Experiment setup: A/B testing, metric definition, success criteria
C6. Product sense: understanding business metrics, unit economics,
    and ROI; connecting model performance to business outcomes

### My weaknesses (W) — score DOWN when these are core requirements
W1. Hardware / physical systems
W2. Enterprise system operations & large-scale serving infrastructure
    (ERP/IT ops, real-time serving <10ms, auction/bidding infra,
    high-QPS systems engineering)
W3. Deep learning research / training novel neural network
    architectures from scratch (CV/NLP model training, GPU training
    pipelines, generative modeling, imitation learning/RL policy
    training). This is about deep-learning-specific research
    expertise — training classical/traditional ML models (XGBoost,
    regression, forecasting) is C1, a strength, not W3.
    NOTE: APPLYING pretrained models, LLM APIs, embeddings,
    fine-tuning via APIs = C2, NOT W3.

### Levels — apply the FIRST test that passes, top to bottom
| Score | Test |
|-------|------|
| 5 | Core problem matches a D AND a C (e.g., recommender/prediction modeling for retail; 0→1 AI product for consumers; ML supporting drug development) |
| 4 | Core problem matches a C in an unfamiliar domain, OR matches a D with an adjacent capability |
| 3 | Generic ML/AI role; no D or C gives an edge, no W is required. DEFAULT for vague postings. |
| 2 | Capabilities partially transfer, but the posting explicitly expects domain knowledge I lack (ramp-up role) |
| 1 | A W-item is a major requirement, though not the whole job |
| 0 | A W-item IS the core job |

### Rules
1. Recruiter/staffing-intermediary check — TWO ways this can trigger,
   score 0 either way and stop (skip the remaining rules):
   1a. "Company industry" states a staffing/recruiting/talent-placement
       industry (e.g. "Staffing and Recruiting"). Deterministic — set
       "confidence": "high".
   1b. The posting's OWN TEXT reveals it directly: recruiter-tell
       phrasing ("I'm partnered with...", "we're partnering with...",
       "on behalf of...", "our client is..."), or the real employer is
       anonymized ("a globally recognised software company"). This is
       inferred from text, not looked up — set "confidence": "medium"
       or "low" and say so in "note".
   Either way: that data belongs to the recruiting intermediary, not the
   real employer, so nothing in the posting can be reliably attributed
   to an actual company.
2. Company industry ≠ role domain. Score the role's problem
   (an e-commerce company hiring for serving infra scores by
   the infra problem).
3. If both a C/D match and a W appear, score by which is the CORE
   responsibility; mention the other in "note".
4. If a posting mentions "training" or "building" models but doesn't
   give enough detail to tell whether it's classical/traditional ML
   (C1) or deep-learning research from scratch (W3) — don't guess.
   Score 3, "confidence": "low", and say what's ambiguous in "note".
5. Multiple matches don't raise the score above the level test —
   list them in matched_* instead.
6. Too vague to identify the core problem → score 3,
   "confidence": "low".
7. Quote evidence fragments BEFORE deciding the score.

### Evidence format
Up to 3 fragments quoted from the posting, each UNDER 10 WORDS,
separated by " | ". Quote the phrases identifying the core problem.

### Examples

Posting: "Company industry/size: Staffing and Recruiting · 11-50 employees

AI Engineer at [recruiting firm]. I'm working with a YC-backed startup
building AI agents for insurance workflows. Building and deploying
multi-agent AI systems in production, RAG and memory systems."
→ Rule 1a: company industry is Staffing and Recruiting → score 0,
   regardless of how strong the JD content looks.
→ {"criterion": "expertise_match",
   "evidence": "Company industry/size: Staffing and Recruiting",
   "matched_domains": [], "matched_capabilities": [],
   "matched_weaknesses": [], "confidence": "high",
   "note": "posted by a staffing/recruiting firm, not the real employer — nothing here can be attributed to an actual company", "score": 0}

Posting: "Senior Machine Learning Engineer at [firm]. We're partnering
with a global consumer technology business to scale a high-impact
Applied AI team building production systems used by millions of
customers."
→ Rule 1b: "We're partnering with a global consumer technology
   business" is recruiter-tell phrasing for an anonymized client.
   Inferred from text, not looked up → confidence medium.
→ {"criterion": "expertise_match",
   "evidence": "We're partnering with a global consumer technology business",
   "matched_domains": [], "matched_capabilities": [],
   "matched_weaknesses": [], "confidence": "medium",
   "note": "recruiter-tell phrasing for an anonymized client", "score": 0}

Posting: "ML Engineer, Seller Analytics at [marketplace co]. Build
models that predict which new product listings will succeed, and
surface trend insights to sellers."
→ D1 + C1 → 5.
→ {"criterion": "expertise_match",
   "evidence": "predict which new product listings will succeed | trend insights to sellers",
   "matched_domains": ["D1"], "matched_capabilities": ["C1", "C3"],
   "matched_weaknesses": [], "confidence": "high",
   "note": null, "score": 5}

Posting: "ML Engineer, Ads Ranking at [e-commerce co]. Improve CTR
prediction and ranking models; experience with recommendation
systems preferred."
→ Modeling role: recommendation/prediction (C1) at a retail
   company (D1) → 5. (Serving infra would be different — see next.)
→ {"criterion": "expertise_match",
   "evidence": "CTR prediction and ranking models | recommendation systems preferred",
   "matched_domains": ["D1"], "matched_capabilities": ["C1"],
   "matched_weaknesses": [], "confidence": "high",
   "note": "CTR specifics are new but problem type matches", "score": 5}

Posting: "ML Engineer, Ads Serving at [e-commerce co]. Build our
real-time bidding and auction infrastructure serving 1M QPS."
→ Core job = serving infrastructure = W2. Rule 2: company domain
   doesn't rescue it. W is the core job → 0.
→ {"criterion": "expertise_match",
   "evidence": "real-time bidding | auction infrastructure | 1M QPS",
   "matched_domains": [], "matched_capabilities": [],
   "matched_weaknesses": ["W2"], "confidence": "high",
   "note": "systems engineering role, not modeling", "score": 0}

Posting: "AI Engineer — build our RAG-powered legal research
assistant. Own retrieval quality, embeddings, and agent workflows."
→ C2 in an unfamiliar domain (legal) → 4.
→ {"criterion": "expertise_match",
   "evidence": "RAG-powered legal research assistant | retrieval quality, embeddings, agent workflows",
   "matched_domains": [], "matched_capabilities": ["C2"],
   "matched_weaknesses": [], "confidence": "high",
   "note": "RAG expertise transfers; legal domain is new", "score": 4}

Posting: "ML Engineer — computer vision for drug discovery. Train
deep learning models on microscopy images to identify candidates."
→ Domain matches (D3) but the core work is TRAINING CV models = W3,
   a major requirement. Rule 3: W governs → 1.
→ {"criterion": "expertise_match",
   "evidence": "train deep learning models | microscopy images",
   "matched_domains": ["D3"], "matched_capabilities": [],
   "matched_weaknesses": ["W3"], "confidence": "high",
   "note": "pharma domain helps but DL training is the job", "score": 1}

Posting: "Senior ML Engineer, Credit Risk. Build underwriting
models; deep knowledge of credit bureau data and lending
regulations strongly preferred."
→ C1 transfers, but domain knowledge explicitly expected → 2.
→ {"criterion": "expertise_match",
   "evidence": "underwriting models | credit bureau data | lending regulations strongly preferred",
   "matched_domains": [], "matched_capabilities": ["C1"],
   "matched_weaknesses": [], "confidence": "high",
   "note": "modeling transfers; credit ramp-up expected", "score": 2}

Posting: "Machine Learning Engineer. Develop and deploy ML models,
collaborate with stakeholders, improve model performance."
→ Rule 6: too generic → 3.
→ {"criterion": "expertise_match",
   "evidence": "develop and deploy ML models | improve model performance",
   "matched_domains": [], "matched_capabilities": [],
   "matched_weaknesses": [], "confidence": "low",
   "note": "posting too generic to identify core problem", "score": 3}

Posting: "Data Scientist — train and deploy models to predict
customer churn, and continuously improve model performance."
→ "Train models" with no detail on technique/architecture — could be
   classical (XGBoost/regression, C1) or deep learning (W3). Rule 4:
   don't guess → 3.
→ {"criterion": "expertise_match",
   "evidence": "train and deploy models to predict customer churn",
   "matched_domains": [], "matched_capabilities": [],
   "matched_weaknesses": [], "confidence": "low",
   "note": "\"train models\" with no technique/architecture detail — can't tell classical ML from deep learning research", "score": 3}

Posting: "ML Engineer, Growth. Own our experimentation platform
roadmap: design A/B tests, define success metrics, and tie model
improvements to revenue impact for our consumer app."
→ D2 + C5 + C6 → 5.
→ {"criterion": "expertise_match",
   "evidence": "design A/B tests | define success metrics | tie model improvements to revenue",
   "matched_domains": ["D2"], "matched_capabilities": ["C5", "C6"],
   "matched_weaknesses": [], "confidence": "high",
   "note": null, "score": 5}

### Output format (JSON, fields in this exact order)
{"criterion": "expertise_match",
 "evidence": "<up to 3 fragments, each <10 words, ' | ' separated>",
 "matched_domains": ["D1"-"D3" or empty],
 "matched_capabilities": ["C1"-"C6" or empty],
 "matched_weaknesses": ["W1"-"W3" or empty],
 "confidence": "high" | "medium" | "low",
 "note": "<nuances, or null>",
 "score": <0-5>}
"""


class ExpertiseMatch(BaseModel):
    """Output shape mirrors the rubric's "Output format (JSON, fields in
    this exact order)" section — field order here is preserved in the tool
    schema handed to the model."""

    criterion: Literal["expertise_match"] = "expertise_match"
    evidence: str = Field(
        description="Up to 3 fragments quoted from the posting, each under 10 words, ' | ' separated."
    )
    matched_domains: list[Literal["D1", "D2", "D3"]] = Field(description="Domains this role's core problem matches, or empty.")
    matched_capabilities: list[Literal["C1", "C2", "C3", "C4", "C5", "C6"]] = Field(
        description="Capabilities this role's core problem matches, or empty."
    )
    matched_weaknesses: list[Literal["W1", "W2", "W3"]] = Field(
        description="Weaknesses that are a core requirement of this role, or empty."
    )
    confidence: Literal["high", "medium", "low"]
    note: str | None = Field(description="Nuances, e.g. a D/C match vs. a W-item tension, or null.")
    score: int = Field(ge=0, le=5)


def get_job_posting(job_id: int = JOB_ID) -> str:
    """Includes Company.industry/size alongside the raw JD text — domain
    matching (D1-D3) needs to know what business the company is actually
    in, which a generic-sounding JD (e.g. a consultancy's "for our clients"
    framing) doesn't always state on its own. Company industry/size are
    captured on 1,297 of 1,301 companies already in the DB (see
    analysis/company_analyzer or equivalent), so this is real signal, not
    a guess."""
    session = get_session()
    try:
        job = session.get(Job, job_id)
        company_line = ""
        if job.company:
            parts = [p for p in (job.company.industry, job.company.size) if p]
            if parts:
                company_line = f"Company industry/size: {' · '.join(parts)}\n\n"
        return f'Posting: "{job.title} at {job.company_name}"\n\n{company_line}{job.raw_text}'
    finally:
        session.close()
