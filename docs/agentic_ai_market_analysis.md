# Agentic AI Job Market Analysis

Ad-hoc analysis run 2026-07-14 against `data/job_hunt.db` (ml_ai track). Not
a maintained report — a snapshot of one query session. Re-run the queries
below against a fresher DB if this goes stale (scraper only pulls `f_WT=2`
remote-tagged LinkedIn postings, see caveat at bottom).

## Scope

- **Salary-filtered pass:** 72 ml_ai jobs paying $200k+ (min or max) that
  mention agent/agentic, any company size.
- **Broad pass:** 695 ml_ai jobs mentioning agent/agentic at companies with
  50+ employees (any salary) — the primary dataset for gap analysis below.
- **Theme pass:** 1,430 ml_ai jobs at companies in Software Development / IT
  Services & Consulting / Technology-Information-Internet / Computer &
  Network Security industries — used for the "what job are they actually
  asking for" breakdown.

## Skill gaps vs. your resume (695-job broad pass)

Resume matched via `screening_results.skill_matched` / `skill_missing`
(computed by `judge/stage1_screen.py` against whatever Resume row was in
the DB at screening time — note the `resume` table is currently empty, so
this reflects the resume used historically, not necessarily what's there now).

**Already strengths — do NOT prioritize, you already show these:**
Agents (89%), LLM (69%), Python (66%), RAG (45%), AWS (38%), Generative AI
(37%), **LLM Evaluation (35%)**, **Observability/production monitoring (31%)**,
CI/CD (29%), Vector DB (25%), GCP (24%), Prompt Engineering (24%), SQL (24%),
LangChain (23%).

**Real gaps, ranked by frequency in these JDs:**

| Skill | % of 695 jobs |
|---|---|
| Multi-Agent Systems (orchestration frameworks) | 32% |
| MCP (Model Context Protocol) | 19% |
| Kubernetes | 18% |
| TypeScript / JavaScript | ~17% each |
| Fine-tuning | 17% |
| MLOps | 14% |
| Responsible AI / guardrails | 13% |
| Microservices | 13% |
| Human-in-the-Loop | 12% |
| REST API | 11% |
| Snowflake / Databricks / AWS Bedrock / Event-Driven Arch / Kafka | 5–11% each |
| Airflow | 5% (smaller than expected) |

**Reading it:** the market isn't asking "can you eval an LLM" — you already
show that. It's asking "can you build and *productionize* a system where
multiple agents call tools, hand off work, and don't cascade-fail," plus
increasingly "do you know MCP" (still early — high signal to show it now).
"Enterprise integration" instinct is real but second-tier: Snowflake /
Databricks / Bedrock / Kafka / event-driven architecture cluster together
= wiring agents into a real company's existing data/infra stack.

**Best ROI move:** one project, not five skills in isolation. A multi-agent
system (LangGraph or AutoGen) with an MCP server exposing tools, a
human-in-the-loop approval step for risky actions, deployed via
Docker+K8s (even minimal EKS/GKE). Hits the top 5 gaps at once using infra
you half-know already (Docker, Terraform, AWS/GCP).

## What type of job are they actually asking candidates to do?

Titles are close to useless — 74% of the 1,430 in-scope jobs have generic
titles ("AI Engineer," "Software Engineer"). The real job function only
shows up in the JD body. Classified by scanning JD text for functional
patterns (not mutually exclusive — most postings are a bundle of several):

| Type of work | % of jobs | What it looks like |
|---|---|---|
| Production ML infra / MLOps | 31% | CI/CD for models, serving infra, monitoring, feature stores |
| Data engineering / pipelines | 28% | Airflow/dbt/Snowflake/Databricks pipelines feeding the AI system — unglamorous plumbing most "AI" roles quietly require |
| Agentic/multi-agent systems | 28% | Multi-stage agentic pipelines, LangGraph/CrewAI/MCP orchestration — JDs explicitly say "not a wrapper around a chatbot" |
| Traditional ML modeling | 21% | Forecasting, ranking, fraud, personalization — classic supervised ML |
| Fine-tuning / model training | 18% | Actual training/RLHF — rarer, clusters at bigger orgs |
| RAG / LLM feature integration | 16% | Bolting LLM features onto an existing product — product-eng more than ML-research |
| AI governance/compliance/security | 16% | Fast-emerging: "agent identity" (Okta), AI governance (OneTrust), HIPAA/FedRAMP-constrained builds |
| Technical pre-sales / solutions consulting | 13% | Selling/demoing/POC'ing AI products, not building |
| Eval / QA / red-teaming | 11% | New specialization: "own evaluation and dataset workstreams," bias/regression testing |
| RPA / no-code automation | 8% | n8n, Power Platform — lower-code glue work, smaller/consulting shops |
| Forward Deployed Engineer (embedded with a customer) | 3% by title, constant in body text | Sit inside an enterprise customer, write production code in *their* environment, own prototype→production for that account. High-paying, and JDs are explicit about not wanting demo-only experience |

**Bottom line:** the modal ask is "take an agentic/LLM system past the
prototype stage" — build the data pipeline it reads from, orchestrate the
multi-agent/tool-calling logic, ship it with production infra, prove it
works with evals — inside a real enterprise's stack, sometimes literally
embedded with that customer's team. This directly reinforces the skill-gap
list above: Multi-Agent Systems, MCP, Kubernetes/microservices aren't
abstract skill-list items, they're what "past the demo" concretely requires.

## Known caveat: Google (and similar) don't appear in this DB

`scraper/linkedin_scraper.py` hardcodes `f_WT=2` (LinkedIn's Remote-only
work-type filter) into every search URL. Google rarely tags postings
"Remote" on LinkedIn (defaults to Hybrid/On-site even for remote-friendly
roles), so its jobs never enter scrape results regardless of keyword,
salary, or company size — not a downstream filter (blocklist/title
filter/relevance), a scrape-time exclusion. Same likely applies to other
companies that default to Hybrid tagging. Fix would be adding `f_WT=3`
(Hybrid) or dropping `f_WT` — a real scraper code change, not something
fixable by re-querying the existing DB.

## Reproducing / extending this

Ad-hoc queries used, not committed scripts. Rough shape:

```python
# gap analysis: join jobs -> companies -> screening_results,
# filter track='ml_ai', is_relevant=1, raw_text/title LIKE agent-ish terms,
# exclude company.size in {"0-1 employees","2-10 employees","11-50 employees"},
# aggregate json.loads(skill_missing) / skill_matched with collections.Counter

# theme analysis: same job set, filter companies.industry to
# software/IT industries, regex-match JD body text against a dict of
# functional-archetype patterns (see conversation for the exact regexes),
# count with overlap allowed (multi-label)
```

If this becomes a recurring need, worth promoting into a proper
`analysis/agentic_market.py` script rather than re-deriving ad hoc each time.
