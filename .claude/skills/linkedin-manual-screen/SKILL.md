---
name: linkedin-manual-screen
description: Manually browse LinkedIn job search via Claude in Chrome so the browser extension screens and scores every listing. Use when the user asks to search/screen LinkedIn jobs for a keyword and time window (e.g. "do ai engineer remote past 24 hours", "literal search 10 pages", "semantic search"), or invokes /linkedin-manual-screen.
---

# LinkedIn Manual Screen

Drives LinkedIn job search in the browser so `extension/` captures, dedups, and LLM-scores each posting into the DB. Claude only navigates and clicks — it never parses LinkedIn's DOM for job data.

Background and history: `docs/manual_browsing_screening.md`. This skill is the operating procedure; read the doc only when something behaves unexpectedly.

## Defaults — do not ask the user to restate these

| | Default |
|---|---|
| **Endpoint** | **LITERAL `/jobs/search/` by default.** Only use semantic `/jobs/search-results/` when the user says "semantic". If they ask for both: literal first, then semantic |
| Pages | **20 per keyword** on the literal endpoint, unless the user says otherwise. Semantic caps at 10 (`start=250` returns nothing) — don't try for more there |
| Location | `geoId=103644278` (United States) |
| Work type | **ALL types by default — omit `f_WT` entirely.** On-site, hybrid and remote are all collected. Add `f_WT=2` ONLY when the user asks for remote specifically ("remote", "remote only", "wfh"). `f_WT=3` is hybrid, `f_WT=1` on-site |
| Window | `f_TPR=r86400` (24h). Week `r604800`, month `r2592000` |
| Coverage | **Click EVERY card that survives the three filters.** No discretionary triage, no "this looks irrelevant", no stopping because a page seems repetitive |
| Pacing | **Never stop between pages.** Run all pages in one continuous turn |
| Reporting | Per page, log skip count + reasons. Give ONE consolidated report at the very end |

## The two standing filters — the ONLY reasons to skip a card

1. **Agency** — `judge.agency_blocklist._matches_agency_substring(company)` or company in the DB-derived "Staffing and Recruiting" list. Never hand-roll this check; it does word-boundary matching so `"turing"` doesn't fire on `"Manufacturing"`.
2. **Staff / Principal in the title** — word-boundary match. Title test only, not a seniority inference.

**On-site is NOT a skip** (changed 2026-09-08). It was, back when every search carried `f_WT=2` and an on-site card could only be LinkedIn leaking one through. Now that all work types are collected by default, skipping them would silently discard exactly what the search asked for. Hybrid was never a skip either — hybrid roles have scored 11–12.

Do **not** filter on company size, perceived relevance, or whether a company was already seen. A seen *company* often has a genuinely new *posting*; job-level dedup is the extension's job.

## Endpoints

| | `/jobs/search/` (literal) | `/jobs/search-results/` (semantic) |
|---|---|---|
| Match | Literal token | Broad/related-term |
| Relevance | Low — matches tokens anywhere | High |
| Depth | Deep (past `start=1225`) | **Caps at 10 pages** (`start=250` → no results) |
| Detail pane JD | **Already in DOM on click** | Lazy — needs a real wheel scroll |
| List cards | `<a href="/jobs/view/…">` | `<div>` with click handlers |

They return **largely disjoint inventory**. When the user wants both, run literal first, then semantic.

## Procedure

### 0. Refresh the blocklist cache

```bash
./venv/bin/python -c "
from db.session import SessionLocal; from db.models import Company
s=SessionLocal()
open('SCRATCH/agencies.txt','w').write('\n'.join(sorted(
  c.name for c in s.query(Company).filter(Company.industry=='Staffing and Recruiting'))))"
```

### 1. Navigate + install helpers (one browser_batch)

`https://www.linkedin.com/jobs/{search|search-results}/?keywords=<kw>&f_TPR=r86400&geoId=103644278&start=<N*25>`

Append `&f_WT=2` only if the user asked for remote specifically. With no `f_WT`, LinkedIn returns all work types.

Install `__sleep __cardEls __panel __pending __waitPanel __retry __track __doC __go __list2` — see `helpers.js` in this skill directory.

### 2. Load all cards — the ONLY `computer` calls you should make

**Literal endpoint:** `computer scroll` down ×2–3 at **(300, 500)** — the LEFT list pane. It lazy-loads 7 → 22-25 cards. Programmatic `scrollTop` does not work; a real wheel event is required. That is the *entire* screenshot budget for the page.

**Semantic endpoint:** all 25 cards are present on load — **no list scroll needed at all.**

### 3. Enumerate and classify

`window.__list2()` → pipe into `classify.py` (see this directory) → prints SKIP lines with reasons and the CLICK list.

Slice the output (`.slice(0,1900)`) if the harness blocks a long query string.

**Record the page before moving on — never skip this, never defer it to the end.**

```bash
./venv/bin/python -m tests_and_eval.collection_report record \
  --session <yyyymmdd-keyword> --keyword "<keyword>" --endpoint literal|semantic \
  --pages <page>:<rendered>:<skipped>:<clicked>
```

`rendered`, `skipped` and `clicked` come straight off the classify output, and `rendered` must equal `skipped + clicked`. Use one `--session` id for the whole run.

Two reasons this is per page and not batched at the end:

- A page that rendered fewer than 25 cards was not fully scrolled, and listings you never enumerated leave **no trace anywhere** afterwards — no row, no null, no error. This is the only moment that miss is detectable.
- Runs die mid-way (the extension disconnects every 20–30 jobs; the renderer goes unresponsive on long sessions). A crashed run is exactly the one whose funnel you need, and recording at the end loses all of it precisely then.

The call is trivial next to the 2–3 screenshots the page already costs.

### 4. Click every CLICK card — one JS call each

```js
await window.__go('<title fragment>', '<company fragment>')
```

**Match on title AND company.** The list is virtualised and re-renders as it scrolls, so indices captured at enumeration drift before the click — index-based clicking silently opens the wrong card. A title-only fragment is also unsafe (`'AI Engineer'` matched Protege instead of The Methodical Group).

Chain 2 per call when both are likely cached; 1 per call otherwise.

### 5. Next page — repeat from step 1. Do not pause to report.

## Token efficiency — this is the whole game

Screenshots dominate cost. `computer` returns a full screenshot on **every** call (~1–2k tokens). Read results from the extension's own shadow DOM (`__panel()`), never a screenshot. One `__go` call per job ≈ 40 tokens.

### The scroll rule, per endpoint — verified live 2026-09-02

| | List pane | Detail pane |
|---|---|---|
| **Literal** `/jobs/search/` | wheel scroll ×2–3 at (300,500) to load 7 → 25 cards | **NEVER scroll.** `About the job` / `About the company` are already in the DOM on click |
| **Semantic** `/jobs/search-results/` | not needed — 25 cards on load | **Required** for uncached jobs. `h2` returns `[]` before scroll, populated after |

This is why literal is the default: a literal page costs **2–3 screenshots total**; a semantic page costs **one screenshot per uncached job** (~15). Measured ~5× difference.

### Cached jobs never need a scroll, on either endpoint

A cached score or a repost-skip requires no extraction, so chain them with no `computer` call at all — 2–4 per JS call:

```js
const out=[];
for (const [t,c] of [['Title A','CoA'],['Title B','CoB']]) {
  await window.__doC(t,c); out.push(await window.__waitPanel(9000));
}
out.map(x=>x.slice(0,70).replace(/\n/g,' | ')).join('\n')
```

**But only for jobs you expect to be cached.** Chaining *uncached* semantic jobs this way returns `Extraction incomplete` and you have to redo them with the scroll — which costs more than doing it right. If a chained result comes back `Extraction incomplete`, that job was not cached: re-click it with the scroll.

## Gotchas

- **45 s CDP cap per JS call.** `__waitPanel(28000)` + a retry branch fits; 30 s + 25 s does not and dies with "Runtime.evaluate timed out". If a job still says `Scoring against your resume…`, call `__waitPanel` again in a fresh call — don't lengthen the budget.
- **Treat as not-yet-final:** `dwelling`, `Scoring against…`, `Watching this job…`, `Untitled`, `NO-EXT`, `NO-PANEL`.
- **`Extraction incomplete` / `TypeError: Failed to fetch`** → click Retry (`__retry()`), wait, re-read. Almost always succeeds on attempt 2; WebstaurantStore needed 3.
- **Extension disconnects every ~20–30 jobs.** Usually the batch *completed* — `tabs_context_mcp` to reconnect, check `typeof window.__go === 'function'`, read the panel before assuming failure. Only reinstall helpers after a real `navigate`.
- **Reinstalling helpers after a navigate is cheap** — stash the helper source in `sessionStorage.setItem('__jhi_helpers', SRC)` on first install; it survives same-origin navigation, so later pages only need `eval(sessionStorage.getItem('__jhi_helpers'))`.
- **Renderer goes unresponsive** after long sessions → `navigate` to the same URL to reset, then reinstall helpers.
- **Results reshuffle** between visits to the same page number, and result counts drift within a session. Coverage comes from DB dedup, not page ordering.
- LinkedIn is retiring the classic endpoint ("gradually retiring classic job search starting in September") — expect instability there.

## Report format (once, at the end)

0. **Run totals — always the first line of the report, never omitted:**

   `SEEN <n> | SKIPPED <n> | CLICKED <n>`

   `SEEN` = every card enumerated across all pages (skipped + clicked). Numbers come from the per-page classify output summed up, not from a guess.

1. **Coverage table** — per page: cards rendered, skipped (count + reason breakdown), clicked.
2. **Scores 10+**, sorted, with company / title / salary / location.
3. **Flags** — new agency candidates (check LinkedIn's own "Staffing and Recruiting" industry field), mis-parsed salaries, recruiter reposts of another company's JD, non-US postings.
4. **Total scored**, verified from the DB, not from memory:

```bash
./venv/bin/python -c "
from datetime import datetime,timedelta
from db.session import SessionLocal; from db.models import Job,Company,ScreeningResult
s=SessionLocal(); cut=datetime.now()-timedelta(hours=6)
q=(s.query(ScreeningResult,Job,Company).join(Job,ScreeningResult.job_id==Job.id)
   .outerjoin(Company,Job.company_id==Company.id)
   .filter(ScreeningResult.screened_at>=cut,ScreeningResult.total_score>=10)
   .order_by(ScreeningResult.total_score.desc()).all())
[print(f'{r.total_score:>2}/15  {((c.name if c else None) or j.company_name or chr(63))[:30]:<30} {j.title[:55]}') for r,j,c in q]"
```

Never report counts from memory — they drift. Query the DB.

5. **Data-quality gate** — both, always, even when clean. See `docs/production_data_quality.md` for what each guarantees.

```bash
./venv/bin/python -m tests_and_eval.collection_report report --session <yyyymmdd-keyword>
./venv/bin/python -m tests_and_eval.ingest_check --hours 6
```

The pages were already recorded one by one in step 3; this only reports them. If `report` says "no pages recorded", pages were skipped during the run — say so in the report rather than backfilling from memory.

`ingest_check` exiting 2 means a BLOCK invariant is violated — something no correct run can produce. **Stop and report it before collecting anything further**; do not screen more jobs on top of a broken invariant.

6. **Extraction health** — always run, always report the line, even when clean:

```bash
./venv/bin/python -m tests_and_eval.extraction_health --hours 6
```

Report it as one line (`extraction: all fields healthy`) unless a field is flagged, in which case run the repair loop below **before** finishing the report.

## Repairing a field the extractor stopped finding

LinkedIn rebuilds this page every few weeks and each rebuild silently kills a field. This loop is how that gets fixed in-session instead of being discovered ten thousand rows later — which is what happened in September 2026, when `workplace_type` went null on 97% of captures for weeks with nobody noticing.

**Trigger:** `extraction_health` flags a field `<-- BROKEN?`, i.e. it is null on ~every one of the last 20 captures. A field that is merely *sometimes* null is not broken — plenty of postings genuinely have no applicant count. Do not start this loop on one null.

1. **Get the evidence.** The DOM that defeated the extractor was saved at failure time — you do not need to re-visit LinkedIn to see it:

   ```bash
   ./venv/bin/python -m tests_and_eval.extraction_health --hours 6 --export
   ```

   That writes each stored snapshot to `tests_and_eval/fixtures/<field>-<date>-<jobid>.html`.

2. **Read the snapshot** and work out where the value lives now. It is real markup from a real failed capture, with hashed class names and svg noise already stripped.

3. **Add a strategy — do not edit an existing one.** In `extension/content/extract.js`, append an entry to that field's list in `STRATEGIES`. Give it a name describing the layout (`meta-parens`, `leaf-parens`). The existing strategies must stay: they are what keeps every previously-solved layout working, and they cost nothing when they don't match.

4. **Never widen a validator to make a value fit.** `FIELD_VALIDATORS` is the guardrail that stops one field being populated with another's value. The worst data incident in this project's history (417 rows, 251 with a location stored as a posted_date, all of them then invisible on the dashboard) was exactly a matcher that accepted whatever it happened to find. If the right value fails the validator, the strategy is wrong, not the validator.

5. **Prove it, both directions:**

   ```bash
   node tests_and_eval/extraction_fixtures.js
   ```

   Every case must pass — the new snapshot flips from `TODO` to `ok`, **and** the pre-September / post-September / Chicago-trap cases still pass. A fix that solves today's layout and breaks a previous one is the failure mode this harness exists to catch.

6. **Reload the extension** (`chrome://extensions` → reload), re-capture one job, and confirm `extraction_health --hours 1` shows the field resolving via the new strategy.

7. **Keep the fixture.** The exported `.html` is now a permanent regression test. Commit it.

**What not to do:** do not rewrite the extractor to "be more flexible", and do not remove a validator to get a field populated. A null is recoverable — the field can still be inferred from `raw_text` server-side (`db/job_writer.py` already does this for `workplace_type`). A silently wrong value is not recoverable, because nothing downstream knows it is wrong.
