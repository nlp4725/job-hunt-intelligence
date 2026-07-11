# Job Hunt Intelligence

Automates job search: scrapes ML/AI and PM listings, enriches company data, and judges each listing's fit against the user's resume and preferences.

## Language

### Job Judge

Scores a job listing to produce a total fit score for ranking, via the Judge Agent's rubric: Skill, Seniority, Domain transferability, Career-narrative fit, and Company research, summing to a Total Score (max 300). Company size and salary were considered and explicitly dropped — see [ADR 0002](docs/adr/0002-drop-salary-and-size-criteria.md). The rubric shape and the split between live scoring and evaluation-only reference scores is recorded in [ADR 0003](docs/adr/0003-judge-agent-unified-rubric.md).

_In progress:_ Seniority and Domain transferability are being rebuilt as standalone, richly-specified rubrics — **Seniority Fit** and **Expertise Match** below — as part of a fast/cheap Stage 1 screen that deliberately excludes Company research (too slow/expensive for a first pass over the whole corpus). Skill and Career-narrative fit haven't been rebuilt yet and still use the original Judge Agent framing above. Not yet wired into `judge_agent.py` itself — currently live only as standalone prompts + eval harnesses under `tests_and_eval/test_seniority/` and `tests_and_eval/test_expertise/`.

**Judge Agent**:
The single LLM call that reads `resume.pdf`, `JD.txt`, `company_report.md`, and the Career Goals statement, and produces every rubric dimension's score plus a qualitative analysis, in one pass — including Skill, which earlier designs treated as a separately-computed deterministic signal (see ADR 0003 for why that changed).
_Avoid_: Skill Match Score, Semantic Match Score (earlier, superseded framing — semantic match is now split across Seniority, Domain transferability, and Career-narrative fit)

**Seniority Fit**:
0-5 rubric scoring whether a role's level of responsibility matches the candidate's actual seniority (~3 years, targeting mid-level) — bands from New Grad/Entry through Principal/Director, keyed primarily off the JD's stated years requirement (half-open bands, "X+ years" → use X, a range → use the minimum) with a fallback to inferring level from responsibility language when no years are stated. Includes an agency/recruiter-posting rule (score 0 regardless of content when the company is a staffing/recruiting intermediary — see Agency Blocklist) and a rule that no-years-stated JDs get inferred, never defaulted. Prompt lives in `tests_and_eval/test_seniority/common.py` (`SENIORITY_PROMPT`).
_Avoid_: Seniority (earlier, vaguer framing — this is the fleshed-out replacement)

**Expertise Match**:
0-5 rubric scoring whether the candidate's background is an advantage for a role's core problem, matched on two axes — Domain (D1 retail/e-commerce, D2 marketing/consumer-to-C, D3 pharma/healthcare/life sciences) and Capability (C1 classical end-to-end ML modeling, C2 building/deploying LLM/RAG/agent systems, C3 pain-point analysis, C4 0→1 product development, C5 experiment design, C6 product sense) — against three Weaknesses (W1 hardware/physical systems, W2 large-scale serving infrastructure, W3 deep-learning research/training novel architectures from scratch). Scores by the role's core problem, not the company's industry or a skills-list buzzword match. Explicitly does not score people-management as a weakness — that's Seniority Fit's job; folding it in here double-penalizes the same signal. Includes the same agency/recruiter-posting rule as Seniority Fit (deterministic via `Company.industry`, plus a text-based fallback for postings the industry field doesn't catch), and a rule that ambiguous "trains models" language (unclear if classical ML or deep-learning research) defaults to a neutral score rather than guessing. Prompt lives in `tests_and_eval/test_expertise/common.py` (`EXPERTISE_MATCH_PROMPT`).
_Avoid_: Domain transferability (earlier, vaguer framing — this is the fleshed-out replacement, now with an explicit Domain × Capability × Weakness structure instead of a loose "strengths/weak areas" list)

**Company Research Agent**:
A worker agent invoked by the Judge Agent (orchestrator-worker relationship, not an independent pipeline stage — the Judge Agent checks for an existing `company_report.md` first and only invokes this agent on a cache miss) that researches Reputation (Glassdoor/Reddit sentiment), Stability (funding stage, layoff/distress signals), and Momentum (recent funding, product/press traction, hiring velocity) via the `web_search` tool, and writes findings to `company_report.md` — a narrative report, not a pre-computed number, cached per company and reused across every job at that company. The Judge Agent reads this report and assigns the Company research rubric score itself, applying the Missing-Dimension Rule when the report notes a dimension has no data.

**Judge Run**:
A batch process that judges every job in `jobs` that doesn't have a score yet (not a single-job on-demand click). Before judging, it filters out jobs whose company is on the Agency Blocklist — those jobs are never sent to the Judge Agent, though they're otherwise untouched (still counted normally in the skills dashboard, trends, etc. — this skip is judge-only, not an `is_relevant`-style global exclusion).

**Agency Blocklist**:
Two detection methods, both checked only at Judge Run time, before a job reaches the Judge Agent:
1. A curated list of known staffing/data-annotation companies (e.g. DataAnnotation, Turing) whose postings are generic contractor gigs rather than real roles at a real employer.
2. A general, deterministic check against `Company.industry` (already captured for 1,297 of 1,301 companies in the DB) — any company classified as a staffing/recruiting industry is excluded the same way. This is the only reliable signal for the recruiter-intermediary problem (`company_name`/`company_id` in our DB being the recruiting firm, not the real end-employer, so its industry/size/Glassdoor data would otherwise get misattributed as if it were the actual employer's) — scanning JD free text for recruiter-style phrasing ("I'm partnered with...") isn't a real filter, just an unreliable heuristic that misses postings worded differently; `Company.industry` is the only field structured enough to filter on deterministically.

Deliberately not folded into `is_relevant`/`title_filter.py`, since those jobs are legitimate scrape results elsewhere in the system — they're just not worth spending a Judge Agent call on.

**Career Goals**:
A short, explicitly-written statement of the candidate's career preferences (e.g. "goal: technical PM, want full product-cycle exposure, prefer building/shipping systems over deep-learning research"), stored separately from the Resume since it records stated preference, not work history. Fed to the Judge Agent specifically to score Career-narrative fit.
_Avoid_: personal interest, interest profile (earlier working names for this same concept)

**Reference Score**:
The ground-truth value a rubric dimension is compared against during the Evaluation Harness, computed by whichever method fits that dimension: a programmatic formula for Skill (`|JD skills ∩ resume skills| / |JD skills|`), a hand-calculated value for Seniority, or a pure human rating for Domain transferability, Career-narrative fit, and Company research. Not a uniform method across dimensions — see ADR 0003.

**Evaluation Harness**:
The calibration process comparing rubric scores against each dimension's Reference Score, using MAE (and MAE rescaled to a 0-100% figure) to find and correct miscalibration — envisioned in ADR 0003 as a 100-sample process for the original 5-dimension rubric. In current practice for Seniority Fit and Expertise Match specifically: a 20-job, human-labeled LangSmith dataset per dimension (`test_seniority` / `test_expertise`), run via `evaluate()` with 3 repetitions per model to also catch run-to-run inconsistency, not just miscalibration — plus cost-per-call and latency, since these two dimensions are meant to run over the whole corpus cheaply. Cost/latency are computed by hand from token counts rather than read off LangSmith's native cost tracking, which has no pricing-table entry for DeepSeek.

**Stage 1 model choice**: DeepSeek V4 Pro, thinking mode explicitly disabled, for both Seniority Fit and Expertise Match — chosen over Claude Haiku 4.5 based on the eval harness results above. Thinking-off DeepSeek was cheaper on every run (~1/15th Claude's cost), and for Seniority Fit was also more accurate (MAE 0.317 vs Claude's 0.333) with tighter, more consistent latency than thinking-on DeepSeek. For Expertise Match, Claude was somewhat more accurate (MAE 0.600 vs 0.700) but DeepSeek's cost/latency advantage was judged worth the small accuracy gap for a first-pass screen. Thinking-on DeepSeek was tested and rejected for both — it was slower, more expensive, and for Seniority Fit specifically *less* accurate than thinking-off, traced to the reasoning trace over-firing the agency-detection rule on legitimate, detailed postings.

**Missing-Dimension Rule**:
When `company_report.md` has no data for one of the Reputation/Stability/Momentum dimensions, the Judge Agent excludes it from the Company research score rather than counting it as zero or a neutral default. This exists so obscure-but-legitimate companies (quiet, profitable, no press) and small-but-early companies (too new for Glassdoor reviews) aren't scored as if "no data" meant "bad" or "average" — only dimensions with actual evidence contribute. A company with data on zero dimensions gets no Company research score at all — excluded from the total, flagged for manual review — rather than a neutral guess. See [ADR 0001](docs/adr/0001-missing-dimension-exclusion.md).
