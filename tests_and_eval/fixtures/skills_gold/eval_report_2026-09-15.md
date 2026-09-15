# Skill extraction eval: JDs, verified gold (2026-09-15)

Pipeline: `extract_skills(normalize(raw_text))`, whole document, current taxonomy.
Gold: the 50 verified JDs in `jds/`. Every gold skill is a taxonomy name. Resumes are not evaluated here.

> Label provenance: every JD's `notes` says "Relabelled by Claude reading the JD (not the pipeline)". If no person has reviewed those edits, the labels are Claude's judgement, and the figures measure agreement with Claude.

This replaces the earlier provisional run against LLM draft labels.

## 1. Result

| | Value |
|---|---|
| Gold skills | 632 (12.6 per JD) |
| Extracted and correct (TP) | 504 |
| Extracted but not gold (FP) | 30 |
| Gold but not extracted (FN) | 128 |
| **Precision** | **0.944** |
| **Recall** | **0.797** |
| F1 | 0.864 |

**Tagging a skill wrongly is rare. Missing skills is the problem:** misses outnumber wrong tags 4 to 1.

## 2. Why skills are missed

### 2.1 Glued capture text is not the main cause

Two things were tested on all 128 misses: splitting glued words ("GeminiRAG" into "Gemini RAG") and mapping the curly apostrophe `’` to `'`.

| Fix | Misses recovered |
|---|---|
| Split glued words | 4 (Flask, Gemini, RAG, LLM) |
| `’` → `'` | 3 (all Master's Degree: "Master’s degree") |

Splitting glued words mechanically is also harmful: it breaks names like `JavaScript` and `PyTorch`, adding 7 wrong tags (Java ×4, SQL ×3) and dropping recall to 0.723. That confirms glued text has to be fixed at capture, not after the fact. Either way, **most misses are pattern gaps in the taxonomy.**

### 2.2 Misses by skill

| Skill | FN | Typical wording the pattern misses |
|---|---|---|
| Stakeholder Management | 12 | "partner with … cross-functional stakeholders", "influence senior … stakeholders" (see §4) |
| Master's Degree | 9 | "MS or PhD", "Masters or Ph.D.", "Master’s degree" (curly apostrophe), "MS/MA" |
| API Design & Development | 9 | "Design and implement RESTful APIs", "Experience developing APIs", "API layer" |
| Model Monitoring | 8 | "deployment, monitoring, drift detection", "model health", "monitoring AI/LLM systems" |
| Agents | 7 | "agent workflows", "agent orchestration", "RAG pipelines, agents" |
| Tool Use / Function Calling | 6 | "tool-use", "tool-using LLMs", "tool integrations" |
| OpenAI API | 6 | bare "OpenAI" in provider lists |
| Prompt Engineering | 5 | "prompt/context engineering", "prompt design", "system prompts", "prompting" |
| Transformers | 5 | "transformers", "transformer backbones", "Vision Transformers" |
| Software Testing, Anthropic API, Data Pipelines, Distributed Systems | 4 each | "test-driven development"; bare "Anthropic"/"Claude"; "inference pipelines"; "distributed storage" |
| LLM-as-a-Judge, Computer Vision, Roadmapping, Data Modeling | 3 each | "LLM-as-judge"; "object detection"; "roadmap development"; "schema design" |

### 2.3 Wrong tags (all 30)

| Skill | FP | Cause |
|---|---|---|
| A/B Testing | 8 | bare `experimentation` variant ("creativity, experimentation", "from experimentation to production") |
| LLM Evaluation | 3 | "evaluation frameworks" / "model evaluation" in non-LLM contexts |
| Generative AI | 2 | "Gen AI" as an org name; "XGen AI" (no word boundary) |
| Copilot | 2 | product names ("within Copilot") |
| JavaScript | 2 | `\bjs\b` matches "Next.js", "Node.js" |
| Git | 2 | "GitHub Copilot", "links (GitHub…)" |
| Responsible AI | 2 | AMD boilerplate "Responsible AI Policy" |
| Redis, Pandas, Speech Recognition | 1 each | no word boundary: "predisposing", "GeoPandas", "(ASR)" |
| Multi-Agent Systems | 1 | "orchestration frameworks like Kubernetes" |
| SQL, AWS, Statistics, PII, Hallucination Detection | 1 each | "SQL Server", "AWS re:Invent", field of study, scam warning, marketing prose |

## 3. Candidate fixes, measured

Each fix was prototyped outside the taxonomy file. Columns show the change on the gold set, and how many JDs each skill tags in a random sample of 6,000 corpus JDs, before → after.

| Candidate | Gold ΔTP | Gold ΔFP | Corpus JDs | Risk seen in sampled new matches |
|---|---|---|---|---|
| **Master's Degree**: `master['’]s`, `masters`, `MS/M.S. (or\|in) PhD/field` | +6 | 0 | 377 → 1,140 | Looks clean ("BS or MS in Computer Science") |
| **A/B Testing**: drop bare `experimentation`; add "A/B experiments", "online experiments" | +1 | −8 | 862 → 229 | Loses "ML Experimentation" setups (acceptable) |
| **Agents**: "agent workflows/logic/orchestration/development", "LLM/autonomous agents" | +6 | 0 | 1,949 → 2,114 | Looks clean |
| **OpenAI API**: bare "OpenAI" (not "Azure OpenAI") | +6 | +2 | 56 → 464 | Also tags company mentions ("the OpenAI mobile team") |
| **Transformers**: bare "transformers", "transformer architecture/backbones/based" | +5 | 0 | 20 → 227 | Watch for electrical "transformers" |
| **Prompt Engineering**: "prompt/context engineering", "prompt design", "system prompts", "prompting" | +5 | +2 | 669 → 897 | `prompting` is the loosest variant |
| **API Design & Development**: "develop/design/build … APIs", "API layer" | +5 | 0 | 344 → 429 | Looks clean |
| **Model Monitoring**: "drift detection", "deployment, monitoring", "model health" | +5 | 0 | 107 → 484 | "deployment, and monitoring" is generic; review a larger sample |
| **Tool Use / Function Calling**: "tool-use/-using", "tool integrations/interfaces" | +4 | 0 | 380 → 507 | Looks clean |
| **Anthropic API**: bare "Anthropic", "Claude" (not Claude Code/Agent SDK) | +4 | +1 | 46 → 596 | Tags products ("Claude Cowork") and company mentions |
| **Distributed Systems**: "distributed storage/platform/cluster" | +4 | 0 | 696 → 710 | Looks clean |
| **Software Testing**: "test-driven development", TDD, "regression suites", "unit, integration, … tests" | +3 | 0 | 456 → 582 | Looks clean |
| **LLM-as-a-Judge**: "LLM-as-judge", "LLM-based graders" | +3 | 0 | 25 → 86 | Looks clean |
| Vector Database ("vector search"), Computer Vision ("object detection", "image segmentation"), Data Modeling ("schema design", "database modeling") | +2 each | 0 | small increases | Looks clean |
| REST API, Human-in-the-Loop, MLOps ("ML Ops"), scikit-learn ("Scikit Learn"), Responsible AI ("responsible, ethical AI", not "Policy") | +1 each | Responsible AI −2 | small | MLOps `\b` loses glued "inMLOpsto" |
| **Word-boundary fixes**: Redis, Pandas, Speech Recognition `\basr\b`, JavaScript not after "." | Pandas −1 | −4 | small decreases | Pandas `\b` drops "GeoPandas", which gold counts as Pandas |
| Multi-Agent Systems: drop "orchestration framework"; Generative AI: `\bgen ai\b` | 0 | −2 | small decreases | Looks clean |

**All candidates together:** precision **0.944 → 0.968**, recall **0.797 → 0.903** (TP 571, FP 19, FN 61).

**Treat 0.903 as optimistic.** The patterns were drafted from these same 50 JDs, so they are fitted to them. Before merging, confirm on JDs that weren't used to write the patterns (another 50 drafted and verified), and check each through `taxonomy-refresh/check_candidate.py`: corpus hit count plus a larger sample of matches.

## 4. Decisions needed (labelling, not patterns)

1. **Stakeholder Management (12 misses, the largest).** Gold tags any "work with / partner with stakeholders". Adding a bare `stakeholders` pattern gains +10 TP but also +11 FP on gold, and moves the corpus from 224 to 2,503 JDs (42%). Almost every JD says it. Either narrow the label rule to explicit management or influence ("stakeholder management/alignment", "influence senior stakeholders"), or make it a stats-only skill that isn't scored.
2. **LLM Evaluation vs generic model evaluation.** The canonical name says LLM, but the `model evaluation` variant also covers classic ML and CV metrics, and gold marks those cases as wrong. Rename the skill, or split it.
3. **Bare provider names (OpenAI / Anthropic / Claude).** Gold counts "OpenAI, Anthropic, or Gemini" as the API skills. Matching bare names also tags company news and product mentions. Decide whether a provider mention counts as a skill.
4. **Pipelines and monitoring.** "training and inference pipelines" → Data Pipelines, and "Datadog for monitoring" → Observability, are labelled gold but are broad. Confirm the rule before writing patterns for them.

## 5. Suggested order

1. Master's Degree (including `’` in the pattern, or `’`→`'` in `normalize()`), the A/B Testing narrowing, and the word-boundary fixes: clear wins, no new wrong tags.
2. Agents, Transformers, Tool Use, API Design, Software Testing, LLM-as-a-Judge, Distributed Systems: gains with no new wrong tags on gold. Confirm on the corpus sample.
3. Prompt Engineering, Model Monitoring, OpenAI/Anthropic: gains, but with new wrong tags or broad corpus growth. Needs the §4 decisions and a larger sample.
4. Resolve Stakeholder Management (§4.1), then verify a held-out JD batch and run `skills_gold_eval --update-baseline` to switch on the gate in `test_skills_gold.py`.
