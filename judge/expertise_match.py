"""
Expertise Match — one of the three Stage 1 screening signals (see
judge/stage1_screen.py, CONTEXT.md "Job Judge"). Canonical home for the
calibrated prompt/schema/scorer; tests_and_eval/test_expertise/common.py
imports from here rather than defining its own copy, so the eval harness
and production always score the exact same prompt.

Model choice (DeepSeek V4 Pro, thinking disabled) is the "Stage 1 model
choice" recorded in CONTEXT.md — chosen for cost/latency despite Claude
Haiku 4.5 being somewhat more accurate on this rubric specifically (MAE
0.600 vs 0.700), per tests_and_eval/test_expertise/run_eval.py.
"""

from typing import Literal

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from pydantic import BaseModel, Field

from judge.structured_retry import invoke_with_retry

from db.models import Job

load_dotenv()

MODEL = "deepseek-v4-pro"

EXPERTISE_MATCH_PROMPT = """## EXPERTISE MATCH (0–5)
Score how much my background is an advantage for this role's core
problem. Match on TWO axes: DOMAIN (industry) and CAPABILITY
(problem type). Score the role's core problem — what the hire
spends most days doing — not the company's industry.

### My domains (D)
D1. Retail / e-commerce / marketplaces
D2. Marketing & consumer (to-C) products
D3. Pharmaceutical / healthcare / life sciences
D4. Other highly-regulated / audit-grade industries (financial
    services regulatory/compliance, government/public sector,
    legal, insurance) — proven via TWO independent data points, not
    one: FDA-regulated pharma (Vertex) and HK government procurement
    compliance (AI Tender Review System). This is a claim about the
    regulated-process PATTERN transferring (audit trails, deterministic
    rules, sign-off gates), NOT insider domain knowledge of banking
    regulations, insurance actuarial rules, or specific legal codes —
    don't let a D4 match imply literal subject-matter expertise I
    don't have.

### My capabilities (C)
(C1 and C5 deliberately retired — classical end-to-end ML modeling
and generic experiment-design work no longer get capability credit
here, sharpening the rubric toward the agentic/governed/eval story.
Numbering below is intentionally non-contiguous rather than
renumbered, to avoid touching every downstream reference for no
functional benefit.)
C2. Building & deploying ML/AI systems end-to-end (LLM/RAG/agent
    systems, cloud deployment, CI/CD, LLM evals, benchmarking, and
    tracing)
C3. Pain-point & market analysis: identifying customer problems,
    sizing opportunities
C4. 0→1 product development: idea → build → launch
C6. Product sense: understanding business metrics, unit economics,
    and ROI; connecting model performance to business outcomes
C7. Governed/compliance AI system design: building AI pipelines
    whose outputs must survive an audit — citation-level
    traceability, deterministic rule verification, mandatory
    human-in-the-loop sign-off. Distinct from C2: C2 is building the
    LLM/RAG/agent system itself; C7 is when the CORE REQUIREMENT is
    that its outputs be auditable/governed, not just that it works.
    A posting can match both (e.g. "governed agentic platform").

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
    regression, forecasting) is NOT W3 either, it just gets no
    capability credit (neutral, not a strength or a weakness — C1
    was dropped).
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
   (no capability credit) or deep-learning research from scratch
   (W3) — don't guess W3. Score 3, "confidence": "low", and say
   what's ambiguous in "note".
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
→ D1 matches, and the pain-point/trend-analysis framing gives C3 —
   but the actual modeling work (classical prediction) has no
   capability credit since C1 was dropped. D + C3 → 5 (C3 counts as
   a real capability match here, distinct from the modeling itself).
→ {"criterion": "expertise_match",
   "evidence": "predict which new product listings will succeed | trend insights to sellers",
   "matched_domains": ["D1"], "matched_capabilities": ["C3"],
   "matched_weaknesses": [], "confidence": "medium",
   "note": "domain and pain-point-analysis framing match; the prediction-modeling work itself has no capability credit under the current rubric", "score": 5}

Posting: "ML Engineer, Ads Ranking at [e-commerce co]. Improve CTR
prediction and ranking models; experience with recommendation
systems preferred."
→ Pure classical prediction/ranking modeling, no pain-point analysis,
   0→1, or governance framing — no capability left that covers this.
   D1 alone, no C → 3, not 5. (This is the direct consequence of
   dropping C1: a posting that would have scored 5 before now caps
   at 3 unless another capability genuinely applies.)
→ {"criterion": "expertise_match",
   "evidence": "CTR prediction and ranking models | recommendation systems preferred",
   "matched_domains": ["D1"], "matched_capabilities": [],
   "matched_weaknesses": [], "confidence": "medium",
   "note": "domain matches but pure classical modeling has no capability credit since C1 was dropped", "score": 3}

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

Posting: "Senior AI Engineer, Credit Risk. Build an LLM-powered
underwriting decision-support agent; deep knowledge of credit
bureau data and lending regulations strongly preferred."
→ NOT D4: this wants literal lending-regulation/credit-bureau
   subject-matter knowledge, the specific thing D4 explicitly
   excludes — D4 is the audit-grade-PROCESS pattern (traceability,
   rules, sign-off gates), not insider financial-regulatory
   knowledge. So domain knowledge is explicitly expected and I don't
   have it. C2 (LLM/agent building) transfers → 2.
→ {"criterion": "expertise_match",
   "evidence": "LLM-powered underwriting decision-support agent | credit bureau data | lending regulations strongly preferred",
   "matched_domains": [], "matched_capabilities": ["C2"],
   "matched_weaknesses": [], "confidence": "high",
   "note": "agent-building transfers; this wants literal credit/lending subject-matter knowledge, not the general regulated-process pattern D4 covers — credit ramp-up expected", "score": 2}

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
   classical (XGBoost/regression, no capability credit) or deep
   learning research (W3). Rule 4: don't guess W3 → 3.
→ {"criterion": "expertise_match",
   "evidence": "train and deploy models to predict customer churn",
   "matched_domains": [], "matched_capabilities": [],
   "matched_weaknesses": [], "confidence": "low",
   "note": "\"train models\" with no technique/architecture detail — can't tell classical ML from deep learning research", "score": 3}

Posting: "ML Engineer, Growth. Own our experimentation platform
roadmap: design A/B tests, define success metrics, and tie model
improvements to revenue impact for our consumer app."
→ D2 + C6 → 5. (Would previously have also cited C5 for the A/B
   testing itself — C5 was dropped, but C6 alone is enough for D+C.)
→ {"criterion": "expertise_match",
   "evidence": "design A/B tests | define success metrics | tie model improvements to revenue",
   "matched_domains": ["D2"], "matched_capabilities": ["C6"],
   "matched_weaknesses": [], "confidence": "high",
   "note": null, "score": 5}

Posting: "Agentic AI Engineer — build governed, auditable agent
workflows for enterprise/government customers. Own the platform's
audit trail, human-in-the-loop approval gates, and traceability from
every automated decision back to its source."
→ Core requirement is that outputs are auditable/governed, not just
   that the agents work = C7, not just C2. Government/public-sector
   customer base = D4 (proven via HK government procurement work,
   not just pharma). D4 + C7 → 5.
→ {"criterion": "expertise_match",
   "evidence": "governed, auditable agent workflows | audit trail, human-in-the-loop approval gates | traceability... back to its source",
   "matched_domains": ["D4"], "matched_capabilities": ["C7"],
   "matched_weaknesses": [], "confidence": "high",
   "note": "government/public-sector customer base matches D4 (regulated-industry pattern, proven via HK govt procurement work); governance/compliance capability matches C7", "score": 5}

Posting: "GRC Automation Engineer — build AI-assisted evidence
collection for SOC 2 / ISO 27001 audits, with mandatory human
sign-off before any control passes and a full audit trail of every
automated determination."
→ C7: little/no LLM-building language, but the core job (auditable,
   human-signed-off automated determinations) is exactly this
   capability. SOC 2/ISO 27001 compliance-automation domain = D4.
   D4 + C7 → 5.
→ {"criterion": "expertise_match",
   "evidence": "AI-assisted evidence collection for SOC 2 / ISO 27001 audits | mandatory human sign-off | full audit trail",
   "matched_domains": ["D4"], "matched_capabilities": ["C7"],
   "matched_weaknesses": [], "confidence": "high",
   "note": "SOC 2/ISO 27001 compliance-automation domain matches D4; GRC-automation core job matches C7 even without explicit LLM/RAG language — note D4 is the regulated-PATTERN match, not literal SOC2/ISO27001 subject-matter expertise", "score": 5}

### Output format (JSON, fields in this exact order)
{"criterion": "expertise_match",
 "evidence": "<up to 3 fragments, each <10 words, ' | ' separated>",
 "matched_domains": [any of "D1","D2","D3","D4" or empty],
 "matched_capabilities": [any of "C2","C3","C4","C6","C7" or empty],
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
    matched_domains: list[Literal["D1", "D2", "D3", "D4"]] = Field(description="Domains this role's core problem matches, or empty.")
    matched_capabilities: list[Literal["C2", "C3", "C4", "C6", "C7"]] = Field(
        description="Capabilities this role's core problem matches, or empty. C1/C5 retired — no longer scored."
    )
    matched_weaknesses: list[Literal["W1", "W2", "W3"]] = Field(
        description="Weaknesses that are a core requirement of this role, or empty."
    )
    confidence: Literal["high", "medium", "low"]
    note: str | None = Field(description="Nuances, e.g. a D/C match vs. a W-item tension, or null.")
    score: int = Field(ge=0, le=5)


def format_posting(job: Job) -> str:
    """Includes Company.industry/size alongside the raw JD text — domain
    matching (D1-D3) needs to know what business the company is actually
    in, which a generic-sounding JD (e.g. a consultancy's "for our clients"
    framing) doesn't always state on its own. Company industry/size are
    captured on 1,297 of 1,301 companies already in the DB, so this is real
    signal, not a guess."""
    company_line = ""
    if job.company:
        parts = [p for p in (job.company.industry, job.company.size) if p]
        if parts:
            company_line = f"Company industry/size: {' · '.join(parts)}\n\n"
    return f'Posting: "{job.title} at {job.company_name}"\n\n{company_line}{job.raw_text}'


def score_expertise_match(posting_text: str) -> ExpertiseMatch:
    llm = ChatDeepSeek(model=MODEL, extra_body={"thinking": {"type": "disabled"}})
    structured_llm = llm.with_structured_output(ExpertiseMatch)
    return invoke_with_retry(
        structured_llm,
        [SystemMessage(EXPERTISE_MATCH_PROMPT), HumanMessage(posting_text)],
        label="ExpertiseMatch",
    )
