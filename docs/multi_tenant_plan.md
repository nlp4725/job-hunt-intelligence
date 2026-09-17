# Multi-tenant plan: from Nasi's local tool to a product other people can use

Status: proposal, 2026-09-12. Nothing here is built yet.

## What has to change, and why

Four things are hardcoded to one user. Everything else is reusable as-is.

| Hardcoded today | Where | Becomes |
|---|---|---|
| One SQLite file at a fixed path | `db/session.py` `DB_PATH` | Postgres, `DATABASE_URL`, `user_id` on per-user tables |
| Nasi's domains/capabilities/weaknesses baked into the prompt string | `judge/expertise_match.py:29`, `judge/seniority_fit.py:31` | A `UserProfile` rendered into the prompt at call time |
| "limited formal on-title experience, prioritizing entry-level" | `judge/seniority_fit.py:32` | The user's own seniority target, from their input |
| Two fixed tracks (`ml_ai`, `pm`) and one skill taxonomy | `db/models.py` `KEYWORD_TRACKS`, `analysis/skills_extractor.py` `SKILL_TAXONOMY` | Per-user search config; taxonomy stays global, LLM extraction covers the long tail |

### The data split that makes this cheap

A job posting is the same posting for everyone. A *score* is per person. Split the schema on that line:

- **Global corpus** (no `user_id`): `companies`, `jobs`, `job_skills`, `scrape_runs`. Scraped once by anyone, read by everyone. Skills extraction, ATS detection, company research and duplicate detection all run **once per job**, not once per user.
- **Per-user**: `users`, `resumes`, `user_profiles`, `screening_results` (keyed `(user_id, job_id)`), `applications`, `chat_messages`, `search_configs`.

This is the single biggest cost decision in the whole design. With N users on overlapping searches, per-job enrichment is amortized and only the 2-LLM-call screen is per-user.

---

## 1. Functions to write

### A. Resume ingest and profile extraction — new module `profile/`

```python
# profile/resume_text.py          — deterministic, no LLM
extract_text(file_bytes: bytes, filename: str) -> str
    # .pdf via pypdf, .docx via python-docx, .md/.txt passthrough. Raises UnsupportedResume.
split_sections(text: str) -> ResumeSections
    # experience / education / skills / projects. Same approach as analysis/jd_sections.py.

# profile/resume_skills.py
extract_skills_taxonomy(text: str) -> list[str]
    # thin wrapper over the existing analysis/skills_extractor.extract_skills — free, deterministic, already tested
extract_skills_llm(text: str) -> LlmSkillExtraction   # pydantic structured output
    # catches the long tail the taxonomy doesn't know. Returns {skills, evidence_span per skill}
merge_skills(taxonomy: list[str], llm: LlmSkillExtraction) -> list[ResumeSkill]
    # union; taxonomy hits marked source="taxonomy" (high confidence), LLM-only marked source="llm"

# profile/build.py               — this is the function that replaces the hardcoded prompt block
build_profile(resume_text: str, user_input: ProfileInput) -> UserProfile
    # ONE LLM call -> domains (D), capabilities (C), weaknesses (W), years_experience,
    # seniority_targets. user_input carries what the user typed: up to 3 target levels, target roles,
    # domains they want to move into. User input WINS over inference on conflict.
render_seniority_block(profile: UserProfile) -> str
render_expertise_block(profile: UserProfile) -> str
    # produce the markdown that currently sits literally in the two prompt constants
profile_fingerprint(profile: UserProfile) -> str
    # sha256 over the rendered blocks — the cache key for "does this need re-screening"
```

`UserProfile` is versioned, not mutated: editing a profile writes a new row with `version = n+1`. Old screening results stay attributable to the profile that produced them.

### B. Parameterize the judges — edits to `judge/`

```python
# judge/seniority_fit.py
SENIORITY_PROMPT_TEMPLATE   # existing text with the opening paragraph replaced by {seniority_block}
score_seniority_fit(posting_text: str, profile: UserProfile) -> SeniorityFit

# judge/expertise_match.py
EXPERTISE_PROMPT_TEMPLATE   # "### My domains (D)" / "### My capabilities (C)" / "### My weaknesses (W)"
                            # sections replaced by {expertise_block}
score_expertise_match(posting_text: str, profile: UserProfile) -> ExpertiseMatch

# judge/screening_run.py
screen_one(user_id: int, job_id: int) -> ScreeningResult
run_screening(user_id: int, filters: ScreenFilters) -> RunSummary

# judge/screening_cache.py                 — NEW, the cost lever
screen_cache_key(job_id, profile_fingerprint, prompt_version) -> str
get_or_screen(user_id, job_id) -> tuple[ScreeningResult, bool]   # (result, was_cached)
    # skips the LLM entirely when job text, profile and prompt version are all unchanged
```

Everything else in the rubric — the agency rule, the contract rule, the evidence format, the retry wrapper — is profile-independent and carries over untouched. `judge/structured_retry.py` needs no change.

### C. LLM evaluation — `tests_and_eval/eval/` (answers "how do we evaluate the results")

You already have the right machinery (LangSmith datasets, 3 reps, MAE per label, cost/latency in the target fn). What changes is that the dataset now has to carry a **profile**, because the prompt is parameterized. Four layers:

```python
# eval/datasets.py
build_golden_set() -> None
    # (profile, posting) -> human label. Reuse test_seniority_n_expertise, adding a
    # `profile` field = today's hardcoded Nasi block, so existing labels stay valid as
    # the baseline. Then add 2-3 SYNTHETIC profiles (a senior backend eng, a mid PM)
    # with ~30 hand-labeled postings each — this is what proves the prompt generalizes.

# eval/scorers.py
mae_scorer(run, example) -> float
within_one_scorer(run, example) -> bool        # the metric users actually feel
schema_validity_scorer(run, example) -> bool   # did structured output parse first try
agency_rule_scorer(run, example) -> bool       # rule 1 must fire on staffing posts — recall, not MAE
evidence_grounding_scorer(run, example) -> bool # every quoted fragment appears verbatim in the posting

# eval/regression_gate.py
run_gate(prompt_version: str, thresholds: Thresholds) -> GateResult
    # CI: block a prompt/model change that pushes MAE above baseline+0.1 on ANY profile,
    # or drops agency recall below 0.95. Per-profile, so a change that helps Nasi and
    # wrecks the synthetic PM fails.

# eval/consistency.py
variance_report(dataset, model, reps=3) -> VarianceReport
    # per-posting score stddev across reps. High-variance postings are the ones to
    # hand-label next — they're where the rubric is ambiguous.

# eval/online.py                               — the only signal that scales past labeling
outcome_correlation(since: date) -> OutcomeReport
    # joins screening_results against application outcomes: of jobs scored 13-15, what
    # fraction got a reply / screen / interview vs. jobs scored 8-10? Per user and pooled.
    # Precision@k, not MAE — no labels needed, the user's own behaviour is the label.
score_drift(window_days: int) -> DriftReport
    # weekly score distribution per rubric; alarm if the mean shifts >0.5 without a
    # prompt change (silent model update on the provider side)
```

Order of trust: the regression gate blocks merges, the golden set says whether it's *right*, the online correlation says whether it's *useful*. Drift and consistency are monitors, not gates.

### D. Auth, sessions, applications — new module `auth/` and `api/`

```python
# auth/cognito.py
verify_jwt(token: str) -> Claims          # JWKS cached, issuer + audience + exp checked
get_or_create_user(claims: Claims) -> User
require_user(fn)                          # Flask decorator -> g.user

# applications/service.py
mark_applied(user_id, job_id, resume_version, note=None) -> Application
record_event(user_id, job_id, stage: Stage, occurred_at, note) -> ApplicationEvent
    # Stage = applied | rejected | recruiter_screen | interview | offer | withdrawn
list_applications(user_id, filters) -> list[ApplicationView]
application_funnel(user_id) -> FunnelStats    # feeds eval/online.py
```

No server-side session store — JWT only. The current `jobs.applied` / `applied_at` / `applied_resume_version` booleans move into `applications` + `application_events`, so status history is kept instead of overwritten. That history is what makes the online eval possible at all.

### E. Local scrape → cloud store

The scraper stays on the user's machine: it needs their logged-in LinkedIn session, and "each user scrapes with their own browser" is the only defensible posture for a tool other people use. What moves to the cloud is storage and scoring.

```python
# ingest/schema.py
class JobCapture(BaseModel)    # ONE schema for both producers: the Selenium scraper
                               # (scraper/run_scrape.py) and the Chrome extension
normalize_capture(raw: dict) -> JobCapture

# ingest/client.py             — runs locally, in the scraper and behind the extension
push_batch(captures: list[JobCapture], api_base: str, token: str) -> PushResult
    # batched, retried with backoff
queue_offline(captures) -> None / drain_offline(api_base, token) -> PushResult
    # local disk queue so a failed push is never lost

# ingest/service.py            — runs in the cloud
upsert_job(capture: JobCapture) -> tuple[Job, bool]   # (job, created) — dedup on LinkedIn job_id
enqueue_enrichment(job_id) -> None                    # SQS; skills/ATS/company/dup detection
enqueue_screening(user_id, job_id) -> None

# api/routes_v1.py
POST /api/v1/captures          # batch, auth'd -> per-item accepted|duplicate|invalid
GET  /api/v1/jobs              # the user's scored feed
POST /api/v1/resume            # presigned S3 PUT, then parse + build_profile
GET/PUT /api/v1/profile
POST /api/v1/applications
GET  /api/v1/applications
```

### F. Schema work

New: `users`, `user_profiles` (versioned), `resumes` (per user, versioned, S3 key + extracted text), `applications`, `application_events`, `search_configs`.
Changed: `screening_results` gets `user_id` + `profile_version` + `prompt_version`, unique on `(user_id, job_id)`. `resume` and `career_goals` tables fold into `user_profiles`.

**`db/session.py`'s hand-rolled `_migrate_*` chain has to go before this ships.** It is 20 functions of `PRAGMA table_info` against SQLite and none of it works on Postgres. Replace with Alembic — one migration that stamps the current schema as baseline, then normal migrations from there.

---

## 2. Local test plan

1. **Postgres locally first, before any AWS work.** `docker compose up postgres`, `DATABASE_URL` env var, Alembic baseline. Verify the existing single-user flow is unbroken against Postgres — this is the riskiest mechanical change and it's free to do locally.
2. **Two-user isolation tests.** `pytest` fixtures for user A and user B sharing one job. Assert: both see the job, each sees only their own screening result, B cannot read A's resume/applications via any endpoint, and the job's skills were extracted once.
3. **Profile round-trip tests.** Three real resumes (Nasi's, plus two public sample resumes for different fields) → `extract_text` → `build_profile` → render → assert the rendered block is non-empty, contains the declared target level, and that the current hardcoded Nasi block is reproducible to within a human's judgment from his own resume. That last one is the honest check that the parameterization didn't lose information.
4. **Prompt-parity eval.** Run the existing `test_seniority_n_expertise` dataset through the *parameterized* prompt with Nasi's profile injected. MAE must match the current baseline (seniority 0.317 / expertise 0.600) within noise. If it doesn't, the templating changed the prompt's meaning.
5. **Multi-profile eval.** The synthetic profiles from `eval/datasets.py`. New baseline, recorded in CONTEXT.md.
6. **Auth locally.** A `FakeVerifier` behind the same `verify_jwt` interface so the API is testable without Cognito; the real verifier is swapped in by config.
7. **Ingest round-trip.** Scraper and extension both → `JobCapture` → `POST /api/v1/captures` against the local Flask app → assert identical rows. Kill the API mid-push, assert the offline queue drains cleanly on retry.

Gate to move on: steps 1–4 green, and the eval gate running in CI.

---

## 3. AWS plan

```
Local (per user)                      AWS
─────────────────                     ────────────────────────────────────────
Chrome extension  ──┐                 CloudFront + S3  (dashboard SPA)
Selenium scraper  ──┼── HTTPS+JWT ──► API Gateway (HTTP API, JWT authorizer)
                    │                        │
                 Cognito ◄── login ──────────┤
                                             ▼
                                    Lambda: api (Flask + Mangum)
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                        RDS Postgres      S3 (resumes,   SQS enrich ─► Lambda: enrich
                        (private subnet)   raw JD text)  SQS screen  ─► Lambda: screen
                              │                              │            (LLM calls)
                        Secrets Manager ◄────────────────────┘
                        (DB creds, ANTHROPIC/DEEPSEEK/TAVILY keys)
```

**Choices and why:**

- **Cognito user pool** with hosted UI. Email/password + Google. Gives you JWTs the API Gateway authorizer validates natively — no session store, no password handling.
- **API Gateway HTTP API + Lambda (Flask via Mangum).** Traffic is bursty and low-volume; Fargate would cost more sitting idle. Move to Fargate only if cold starts on the dashboard become the complaint.
- **RDS Postgres**, private subnets, `db.t4g.micro` to start (~$13/mo), Serverless v2 later if load justifies it. Not DynamoDB: the queries here are relational and analytical (score distributions, funnel joins), which is exactly DynamoDB's weak spot.
- **LLM calls never on the API path.** `POST /captures` returns immediately; enrichment and screening go through SQS to worker Lambdas (15-min timeout, reserved concurrency capped so a scrape burst can't run up a $400 LLM bill). DLQ on both queues, alarm on depth.
- **S3** for resume files and raw JD text. Resume upload is a presigned PUT straight from the browser — the file never passes through Lambda. SSE-KMS, versioning, lifecycle to delete on account deletion.
- **Secrets Manager** for the DB credentials and every LLM key. The current `.env` at the repo root must not be the deployment mechanism, and should be checked that it never got committed.
- **LangSmith stays** for tracing and the eval datasets; CloudWatch for logs, metrics, alarms (SQS depth, Lambda errors, 5xx rate, RDS connections, daily LLM spend).
- **IaC: AWS CDK in Python.** Matches the stack, one language, and the Lambda bundling story is the least painful.

**Rollout order:**

| Milestone | Contents | Done when |
|---|---|---|
| M0 | Alembic baseline; SQLite → Postgres locally | Existing flow passes on Postgres |
| M1 | `users`, per-user tables, two-user isolation tests | Isolation suite green |
| M2 | Resume ingest + `build_profile` + parameterized judges | Prompt-parity eval matches baseline |
| M3 | Eval layer + CI regression gate | Gate blocks a deliberately-bad prompt |
| M4 | Auth + `/api/v1` + ingest client; extension points at a configurable base URL | Round-trip works locally end to end |
| M5 | CDK stack, deploy to a dev account, migrate own data | Nasi's own daily use runs on AWS |
| M6 | Async workers, alarms, cost caps, account deletion | Second real user onboarded |

**Rough monthly cost at ~20 users:** RDS t4g.micro $13, Lambda + API Gateway <$5, S3/CloudFront <$3, Cognito free under 50k MAU, NAT gateway $32 (or use VPC endpoints and skip it). LLM is the variable: ~2 calls × ~$0.002 per job screened; the global-corpus split and `screening_cache` are what keep it from scaling as users × jobs.

**Three things to decide before M5, not after:**

1. **LinkedIn's ToS.** Scraping it is against LinkedIn's user agreement. Keeping the scrape local and session-bound to each user — which this design already does — is the defensible posture, but shipping it to other people raises the stakes from "personal tool" to "service." Worth a decision (and probably a term of use) rather than a default.
2. **Resumes are PII.** Encryption at rest, a real account-deletion path that purges S3 + Postgres + LangSmith traces, and no resume text in logs or LLM traces you don't control.
3. **Whose LLM key.** Either you eat the cost (needs the concurrency caps and a per-user daily quota from day one) or users bring their own key (kills the cost risk, adds a key-storage problem). Decide before the workers are written; it changes their shape.
