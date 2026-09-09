# Production data quality & error handling

How this pipeline is validated, what it guarantees, and what to do when a guarantee breaks.

Scope: LinkedIn collection (Selenium scraper + browser extension) → screening (LLM) → dashboard → application tracking.

---

## 1. Why this exists

Every rule below was written after a specific failure, not in the abstract. The failures share one shape: **the system produced a confident wrong answer and nothing noticed for weeks.**

| Date | Failure | Cost | Root cause |
|---|---|---|---|
| 2026-08-17 → 09-04 | `/ago/i` matched "ChicAGO" | 417 rows corrupted; 251 stored a location as `posted_date`, then vanished from the dashboard because the date wouldn't parse | A matcher that accepted whatever it found, with no validation of the *shape* of what it accepted |
| 2026-09 layout change | `workplace_type` stopped being read | NULL on 97% of new captures for weeks; ~10K rows needed offline reconstruction | No per-field health signal; a console warning nobody read |
| 2026-08-14 | Detail fetch returned empty dicts | 61 jobs permanently stuck, retry logic never requeued them | `detail_fetched=True` set unconditionally, so failure marked itself successful |
| 2026-09-08 | Workplace backfill re-stamped `last_seen_at` | 3181 rows' post dates jumped forward; dashboard showed 4751 "fresh" jobs instead of 1813 | `onupdate=utcnow` fires on *any* write, and a relative date was anchored to it |
| ongoing | Duplicate-capture merge writes `"0 hours ago"` | 349 rows have an unrecoverable date anchor | A sentinel written to satisfy a non-null expectation, destroying the real value |

**The pattern:** in all five, a null or a loud crash would have been *cheap*. A plausible wrong value was expensive, because everything downstream trusted it.

---

## 2. Principles

**2.1 A null is recoverable. A wrong value is not.**
Nothing downstream can tell that a wrong value is wrong. Never fabricate data to satisfy a non-null expectation — the `"0 hours ago"` sentinel permanently destroyed 349 real timestamps that way. If you don't know, record that you don't know.

**2.2 Validate the shape, not just the presence.**
`posted_date is not None` was true for all 251 corrupted rows. The field held `"Chicago, IL (Remote)"`. Every field needs a predicate describing what a *valid* value looks like, enforced at the write. See `FIELD_VALIDATORS` in `extension/content/extract.js`.

**2.3 Separate absolute invariants from rates.**
A dangling foreign key is a bug — there is no acceptable rate, and any occurrence is a BLOCK. A missing `workplace_type` is a fact about LinkedIn — some postings genuinely don't state one. Demanding "never null" there would only pressure the system into inventing values. Track it as a rate against a baseline, and alert on the rate *moving*. Conflating these two produces either alert fatigue or false confidence.

**2.4 Record provenance for every derived value.**
`workplace_type_source` distinguishes LinkedIn's own tag (1464 rows) from a raw-text inference (3182) from a title guess (411). Without it you cannot tell a data shift from a code regression, and you cannot ever re-derive the inferred ones when the heuristics improve. Every field that can be inferred needs this.

**2.5 Detect while the evidence still exists.**
When extraction failed, the DOM that defeated it was gone forever, so repair meant re-visiting LinkedIn and hoping to hit the same layout. `ExtractionEvent` now stores a scrubbed snapshot at the moment of failure. The general rule: capture diagnostic context at the point of failure, because the failure is usually noticed somewhere else entirely.

**2.6 Telemetry must never break the pipeline.**
`_record_extraction_event` is wrapped in a bare `except` that rolls back and logs. A malformed metadata blob from a stale extension version costs a telemetry row, never a job.

**2.7 A miss that leaves no trace must be caught at the moment it happens.**
Most errors leave evidence in the data. A search page that only rendered 19 of 25 cards leaves *nothing* — no row, no null, no error, and the DB looks perfectly healthy. Those can only be caught in-flight, which is why `collection_pages` records `rendered` per page.

**2.8 Implicit defaults are landmines.**
`onupdate=utcnow` looked like a convenience and silently re-stamped 3181 rows during an unrelated backfill. Prefer an explicit write at the site that means it. If a column changes value without the calling code mentioning it, that column will eventually be changed by code that didn't mean to.

**2.9 Bulk writes follow a fixed sequence.** Back up → dry-run → review the row count and a sample → apply → re-verify. The 2026-09-08 corruption was recoverable *only* because a backup happened to exist.

---

## 3. Invariants

Enforced by `tests_and_eval/ingest_check.py`. Exit codes: `0` clean, `1` warnings, `2` BLOCK.

### BLOCK — no correct execution can violate these

| Invariant | Rationale |
|---|---|
| `duplicate_of_job_id` resolves to an existing job | repost link outliving its target |
| every `screening_result` has a job | a score you paid for and cannot see |
| `company_id` resolves to an existing company | |
| `job_id` is unique | upsert key; dedup and repost counts depend on it |
| at most one `screening_result` per job | double billing; undefined winner |
| `workplace_type ∈ {Remote, Hybrid, On-site}` or NULL | dashboard filters match exact strings |
| `total_score = skill + seniority + expertise` | ranking derives from the sum |
| each component score ∈ 0..5 | an out-of-range LLM score skews every ranking |
| `first_seen_at ≤ last_seen_at` | |
| `applied_at ≥ first_seen_at` | cannot apply to a job before collecting it |
| `posted_date_seen_at` not in the future | a future anchor breaks every relative date |
| `detail_fetched=1` ⟹ title and raw_text present | that flag *asserts* both were captured |
| `applied=1` ⟹ `applied_at` set (rows after 2026-07-29) | 204 older rows predate the column; that data was never recorded |

### WARN — best-effort fields, tracked as rates

Baselines measured 2026-09-08 over 12,470 detail-fetched rows. Alert fires at **baseline × 1.25**.

| Field | Current | Meaning of a rise |
|---|---|---|
| `workplace_type` unresolved | 52.6% | extraction drift, or a shift in what's being posted |
| `location` missing | 4.0% | top-card scope broken |
| `company.size` missing | 3.3% | About-card not rendering |
| `company_name` missing | 1.7% | company link selector broken |
| `posted_date` missing | 0.1% | **most urgent** — dashboard drops these rows entirely |
| `posted_date_seen_at` missing | 2.8% | frozen at 349 rows; any growth is a new bug |
| applied job with no company | 0.4% | uncountable in the per-company tally |

**Baselines are changed deliberately, with a recorded reason, only when a real improvement lands — never to silence a check.**

---

## 4. The collection funnel

Two questions any ingestion system must answer: *did we see everything we should have*, and *what happened to what we saw*.

```
pages rendered (25/page)  →  skipped (filters)  →  clicked  →  job rows  →  screened
```

- `rendered` must equal 25 on every page but the last. Less means an incomplete scroll, i.e. listings never seen.
- `rendered = skipped + clicked` must hold exactly. A gap means cards were enumerated and then lost.
- `clicked → screened` may shrink legitimately (cached re-visits aren't rescored). A *large* gap means captures are failing between the extension and the DB.

Recorded per page by `tests_and_eval/collection_report.py`; `scrape_runs` covers only the legacy Selenium path (last entry 2026-08-17).

---

## 5. Runbooks

### After every collection run
```bash
./venv/bin/python -m tests_and_eval.collection_report report --session <id>   # funnel
./venv/bin/python -m tests_and_eval.extraction_health --hours 6               # per-field drift
./venv/bin/python -m tests_and_eval.ingest_check --hours 6                    # invariants
```
Report three lines: total collected, funnel balance, extraction health. A BLOCK failure stops the run — do not collect more on top of a broken invariant.

### After marking jobs applied
```bash
./venv/bin/python -m tests_and_eval.ingest_check
```
Confirms `applied_at` was stamped, remote/non-remote is classifiable, and the per-company tally is consistent. The dashboard updates `applied_at` and `company_applied_count` from the PATCH response, so both must be right without a reload.

### Before and after any bulk write
```bash
cp data/job_hunt.db data/job_hunt.db.bak-$(date +%Y%m%d-%H%M%S)
python <script> --dry-run     # review row count and a sample
python <script> --write
./venv/bin/python -m tests_and_eval.ingest_check
```
Never skip the backup. It is the only reason the 2026-09-08 incident was recoverable.

### On a BLOCK failure
1. **Stop writing.** Don't collect or backfill on top of a violated invariant.
2. Identify the affected rows and whether the cause is still live.
3. Check whether a backup predates the damage.
4. Fix the *writer*, then repair the data, then re-run the gate.
5. Add a regression test that fails on the old behaviour.

### On extraction drift
See `.claude/skills/linkedin-manual-screen/SKILL.md`, "Repairing a field the extractor stopped finding". Add a strategy; never widen a validator to make a value fit.

---

## 6. What can and cannot be scheduled

| Job | Mechanism | Unattended? |
|---|---|---|
| Data-quality gate, extraction health, funnel report | plain Python on the local DB | yes — no Claude, no browser |
| Selenium scraper (`com.jobhunt.scrape`) | launchd | yes |
| **Manual LinkedIn screen** | interactive session + paired Chrome | **no — structurally impossible** |

The manual screen was scheduled from 2026-09-03 and never once succeeded (23
runs, 0 successes). Three separate causes, discovered in this order:

1. The wrapper trusted `$?`, so a failed run reported `exit=0` and every log
   carried a bogus "creds file: ABSENT" — the diagnostics pointed at a
   non-problem for six days.
2. macOS TCC. The project lived in `~/Desktop`, a protected folder, and a
   LaunchAgent whose program lives there is denied access to it. The venv
   interpreter could not read its own `pyvenv.cfg`, so it died before running
   any project code, and `claude` failed identically. Fixed by moving the
   project to `~/job_hunt_intelligence` (home is not protected).
3. The real blocker underneath both: **the screen needs Claude in Chrome, which
   pairs with a specific interactive Claude Code session.** It is not an MCP
   server you can configure — `claude mcp list` does not show it — so a
   `claude -p` session has no browser tools at all. Cloud-scheduled agents
   (`/schedule`) fail for the same reason plus no access to the local DB.

So `com.jobhunt.manualscreen` is unloaded and its plist archived under
`scraper/disabled/`. Do not re-enable it: it cannot work, and while loaded it
overwrote `last_run_status.json` with a failure every 5.1 hours, pinning the
dashboard red.

Run the screen interactively instead: `/linkedin-manual-screen <keyword>` in a
session with Chrome connected.

A periodic health checker was considered and deliberately NOT built. The
dashboard's `/api/health` already computes staleness, incomplete-run detection
and extraction drift live on every page load, so a timer would add only a push
notification — and running the gate against an idle database on a schedule
returns the same answer every time, which is how an alert gets ignored.

## 7. Error handling: the principles, and the incident behind each

Written down because every one of these was learned from a specific failure in
this repo, with numbers. They generalise; the examples are what make them
concrete.

**7.1 Automation raises the stakes; it does not create reliability.**
The scheduler ran faithfully every 5.1 hours for six days and produced nothing,
while looking exactly like a working system. The property that matters is not
*automatic*, it is *observable and recoverable*.

**7.2 Classify a failure before handling it.** Three kinds, three responses:
transient (extension disconnects every 20-30 jobs) -> retry; permanent (Claude
in Chrome cannot pair with a headless session) -> fail loudly, never retry;
corruption (a location stored in posted_date) -> stop, quarantine, repair.
The old wrapper treated all three identically — `exit=1`, log, move on — so an
architectural impossibility looked like a hiccup for six days.

**7.3 Retry is only safe if the operation is idempotent.** When the 2026-09-08
run restarted, page 1 was recorded twice and every total doubled. Retrying a
non-idempotent write does not fix a failure, it manufactures a new one. The
upsert on (session_id, page) is what makes the retry safe; save_new_job upserts
on job_id for the same reason. Before adding a retry, ask what happens if the
operation runs twice.

**7.4 Detect absence, not just bad events.** Silence is indistinguishable from
health unless absence is modelled explicitly — hence the `stale`, `no-data` and
`incomplete` states, and the pages_planned column, which exists purely so "14
pages recorded" can be read as "stopped at 14 of 40".

**7.5 The check must live outside the thing it checks.** A health check inside
a process disappears exactly when that process dies: report steps 5-6 never run
if the session dies at page 14. Control plane outside data plane.

**7.6 Bound the blast radius.** Checking only at the end means a break at page 3
corrupts 37 more pages. The every-5-pages extraction check is a circuit
breaker. The general question: how much damage accrues between failure and
detection?

**7.7 Degrade honestly; never fabricate.** When extraction fails, job_writer
infers workplace_type from raw text and RECORDS that it did
(workplace_type_source) — precision lost, field kept, provenance intact. The
anti-pattern sits right beside it: the "0 hours ago" sentinel, invented to
satisfy a non-null expectation, destroyed 349 real timestamps.

**7.8 Keep alerts credible.** BLOCK vs WARN, and baselines measured rather than
guessed. Three known-stale violations firing on every run would have trained
the operator to ignore the gate within a week, which is why they were cleared
before it was switched on.

**7.8b An inferred cause is not a cause.** The company-industry failure was
first diagnosed from its signature alone — company_size present, industry null,
which only one code path could produce — and a positional rule (`i > 0`
skipping leaf zero) was "fixed" and written up as the cause. It was not. Real
snapshots, captured once the field was finally instrumented, showed the actual
shape: LinkedIn renders the industry as a BARE TEXT NODE beside sibling spans
for smaller companies, so a childless-element scan could never see it, while
the size span next to it resolved fine. The signature was consistent with both
explanations and the reasoning felt airtight; it was still wrong. Diagnose from
captured evidence, and when acting on inference instead, say so and go back for
the evidence — the repair loop in the skill exists precisely to make that cheap.

**7.9 A check only sees what you told it to look at.** Instrumentation coverage
is itself something to audit. company industry silently went from 8.4% missing
to 32% during the 2026-09-08 run and neither guard noticed, for two independent
reasons: it was not among ingest_check's invariants, and extension/content/
extract.js never recorded it in the per-field telemetry, so extraction_health
was structurally blind to it. Both were omissions rather than decisions. The
cost was not cosmetic — the agency blocklist is derived from
companies.industry, so a company with no industry can never be recognised as a
recruiting intermediary, and its postings consume LLM screening calls. When
adding a field, add it to the writer, the validator, the telemetry and the
gate; a field present in only the first is unmonitored by construction.

## 8. Known gaps

Stated rather than quietly tolerated:

- **`workplace_type` NULL conflates two things** — "LinkedIn didn't say" and "we failed to read it". These need different responses. Recommend an explicit `Unknown` value so NULL means only "never looked", making the extraction-failure rate directly measurable.
- **349 rows have no `posted_date_seen_at`** and fall back to `last_seen_at`. Unrecoverable — the anchor was destroyed before it was ever recorded.
- **204 applied rows have no `applied_at`** (pre-date the column). Excluded from the BLOCK check by an explicit date cutoff.
- **3 open BLOCK violations** as of 2026-09-08: 2 dangling `duplicate_of_job_id` → job 3235 (deleted), 3 orphaned `screening_results` (jobs 3072/11586/12906, deleted), 1 `applied_at` at exact midnight before `first_seen_at` (DevRev, manually set).
- **No LLM-call telemetry.** Schema-valid-response rate, retry rate, abstention rate and cost per run are not recorded, so a degrading model or a rising malformed-JSON rate is invisible. This is the largest remaining blind spot.
- **No alert delivery.** Checks must be run; nothing pages you. Acceptable for a single-operator system, but it means the runbook above is the control.
