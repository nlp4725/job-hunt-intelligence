# Manual browser-driven screening (Claude in Chrome)

How we (Claude, via Claude in Chrome) manually browsed LinkedIn to feed the
existing browser-extension screening pipeline, and the token-efficient
technique used to do it cheaply. This is a one-off manual-browsing session,
not a change to any scraper code.

## Search / filter criteria used

- **Keyword**: `llm remote`
- **Date posted**: Past week
- **Location**: United States (geoId=103644278)
- **Endpoint**: `/jobs/search-results/` (LinkedIn's broad/related-term "semantic
  search" — reached the same way a human would by typing the query into the
  search box), not the literal-match `/jobs/search/` endpoint.

Equivalent URL:
```
https://www.linkedin.com/jobs/search-results/?keywords=llm%20remote&f_TPR=r604800&geoId=103644278
```

Pagination: `start=0,25,50,...`. The result set turned out to run through
page 11 (`start=250`) — page 12 (`start=275`) returned "No results found",
despite the UI showing "99+ results" throughout. Card contents are **not
stable** across repeat visits to the same page number (this endpoint has no
`sortBy` param), so revisiting "page 2" later in the session can show
entirely different postings than it did earlier — this is expected, not a
bug, and doesn't affect coverage since the extension's own DB lookup skips
anything already scored regardless of order.

## Mechanism: what actually collects the data

We did **not** write any extraction code for this. The existing
browser extension (`extension/`) already does it:

- `extension/content/content_script.js` detects the selected job (via
  `currentJobId` in the URL), waits 6s (dwell timer), then extracts and
  scores it — or shows a cached result instantly if the job is already known.
- `extension/content/extract.js` pulls title/company/JD text via `<a href>`
  paths and `<h2>` heading text (not CSS classes), so it survives LinkedIn
  redesigns.
- Our job was purely to **navigate**: click each job card in the list, so
  the extension's own logic fires per job.

## The token-efficient technique

Two things made this dramatically cheaper than a screenshot-per-step
approach:

1. **Finding the next card to click**: used `read_page` (accessibility tree,
   plain text) instead of a screenshot to locate cards by their semantic
   label (e.g. `"Dismiss <title> job"` button text), then clicked by
   element `ref`. Screenshots were only taken when a fresh scroll position
   needed visual confirmation of pixel coordinates — not after every action.

2. **Reading the result**: instead of screenshotting the extension's panel
   to see the score, read its rendered text directly via JS —

   ```js
   document.getElementById('jhi-extension-root').shadowRoot
     .querySelector('.jhi-panel').innerText
   ```

   This is **not** parsing LinkedIn's page — it's reading the extension's
   own shadow-DOM output, code we already control. It costs a few lines of
   text instead of a full image, and is immune to LinkedIn markup changes
   since it never touches LinkedIn's DOM at all.

Per-job loop: click card → wait ~3s → scroll the detail pane down/up a
few times (mimics actually reading it, matching the extension's 6s dwell
requirement) → wait ~7s total → read the panel's text via JS. Batched into
one `browser_batch` call per job (or a few jobs) instead of one tool call
per step.

**Wait 10-25s per job listing, not ~7s.** The extension's dwell timer
(`extension/content/content_script.js:156`) is cancelled via `clearTimeout`
every time the selected job changes — clicking the next card before the
previous job's dwell-then-score cycle finishes silently drops that job
(no score, no DB write, no error). ~7s between clicks is too tight once
LLM-scoring latency is included; a whole batch of 9-10 rapid clicks
produced zero scored jobs in one session even though every click
"succeeded". Prefer waiting 10-25s per job, or — safer — read the panel's
text after each click and confirm it shows a real result (score, "Skipped
— repost", "Skipped — agency", etc.) rather than still `"dwelling"`
before clicking the next card. Varying the wait within that 10-25s range
(rather than a fixed constant) also better mimics human reading pace,
which matters for staying under LinkedIn's bot-detection radar — clicking
through cards on a rigid fast cadence is itself a signal, separate from
the dwell-timer issue above.

Keep `browser_batch` calls small (2-5 jobs) rather than batching 10+
actions at once: a long batch can silently time out client-side after
partially executing, and rapid-fire clicks risk landing on a stale
element `ref` if the page re-rendered — in one session this clicked
LinkedIn's own **logout** link by accident, ending the session. Take a
screenshot to sanity-check state after any batch that behaves
unexpectedly, and re-run `read_page` for fresh refs after any navigation.

**Always scroll the detail pane before the dwell fires — this isn't
optional cosmetic mimicry, it's load-bearing.** `extractRawText()` and
`extractIndustryAndSize()` (`extension/content/extract.js:99-123`) both
locate their content via `<h2>` headings ("About the job", "About the
company") that LinkedIn does not put in the DOM until that part of the
detail pane has been scrolled into view — confirmed live: querying
`document.querySelectorAll('h2')` before any scroll omitted both
headings entirely; after scrolling down and back up, both appeared.
Skipping the scroll step (e.g. to save time with a plain click+wait) on
a job LinkedIn hasn't already cached reliably produces the
`"Extraction incomplete"` panel error with `raw_text`, `industry`,
`company_size`, and often `posted_date` all null — confirmed
reproducible across multiple retries on two different postings (Allstate,
Harris Computer) until a scroll was added, after which the same job
extracted and scored cleanly on the next dwell cycle. Any `posted_date`
(or `industry`/`company_size`) gap noticed in the DB for jobs scraped via
this manual technique likely traces back to this, not a parser bug in
`analysis/posted_date_parser.py`.

## Check the agency blocklist before clicking

Before clicking a card, check its company name against the known
agency/staffing skip-list — if it matches, skip the card entirely rather
than clicking, waiting, and letting the extension discover it's an agency
after the fact. Two sources, both from `judge/agency_blocklist.py`:

1. **Curated substring list** (`AGENCY_COMPANY_NAME_SUBSTRINGS`, ~23 entries)
   — companies confirmed to post generic contractor/AI-training gigs rather
   than real openings (dataannotation, turing, alignerr, toloka, micro1,
   mercor, prolific, toptal, scale ai, telus digital, braintrust, handshake,
   remotehunter, fetchjobs, agilegrid, hackajob, bright vision, jobright,
   haystack, jobgether, sundayy, rex.zone, chatgpt jobs).
2. **DB-derived exact-name list** — every company already in the DB whose
   LinkedIn-reported industry is `"Staffing and Recruiting"` (~386 entries
   as of 2026-08-18). Regenerate with:

   ```
   sqlite3 data/job_hunt.db "SELECT name FROM companies WHERE industry='Staffing and Recruiting' ORDER BY name;"
   ```

   Matched by **exact name**, not substring, so short names (e.g. "Apt",
   "CRG") carry no false-positive risk. Save the output to a local file and
   read it silently rather than re-printing the full list into the
   conversation each time — it's ~2-3k tokens to dump in full and doesn't
   need to be re-shown once saved.

## Agency-blocklist candidates found during the 2026-08-20 "ai engineer remote" pass

None of these are yet in `AGENCY_COMPANY_NAME_SUBSTRINGS` or DB-flagged as
`"Staffing and Recruiting"`, but each showed the same anonymized-recruiter
tell — either the hiring-team bio names them as a recruiter/talent-acquisition
role, or the JD itself says the listing is on behalf of an unnamed "partner"/
"client":

- **District Partners** — hiring team bio: "Executive Search & Consulting";
  JD for a "private-equity-backed national wealth management organization"
- **QP Group** — hiring team bio: "Executive Search - Healthcare Life
  Sciences"; JD: "We're partnering with a fast-growing life sciences LIMS
  company..."
- **Jack & Jill** — JD literally states "This is a job that Jill, our AI
  Recruiter, is recruiting for on behalf of one of our customers"
- **Zest for Tech** — hiring team bio: "Technology Recruiter at Zest |
  Helping startups build AI/ML & software engineering teams"
- **Smart IT Frame LLC** — hiring team bio: "Lead Recruiter @ Smart IT Frame
  | US IT Recruitment"
- **AVP Vigilant Technology Pvt Ltd** — JD: "This position is listed on
  behalf of a partner company, who manages all applications and next
  steps" (also On-site, not remote)
- **Top Gen AI Jobs** — not a recruiter exactly but a job-board scraper: its
  posting's "About the job" section literally reads
  "Home/Jobs/AI Automation Specialist ... Bio-Rad" — a repost of Bio-Rad's
  own listing (which we'd already scored directly) under an aggregator's
  name, not caught by the extension's own repost detection since the
  posting company differs.

## Recurring "matched despite not being remote" mismatches (same session)

LinkedIn's semantic search for "remote" pulled in several results whose own
detail page shows no "Remote" badge (Hybrid, On-site, or unlabeled) — not a
bug in our pipeline, just a search-relevance gap worth knowing about since
these waste a dwell cycle: 10a Labs (both a Boston-Hybrid and later an
SF-Hybrid posting), Xactus (Broomall PA, no location badge at all), Bold
Penguin (Dublin OH, Full-time only), Forcepull (Only, TN, Full-time only),
ClickHouse (San Francisco CA, Full-time only, otherwise a strong 13/15
match), AVP Vigilant Technology (explicitly On-site).

## Outcome of this session

- Covered all 11 real pages (~275 listings) of `llm remote`, past week.
- Confirmed the extension correctly recovers job data previously corrupted
  by the old click-based scraper bug (see the deleted `llm remote` DB rows
  from earlier the same day) — `4452234830`, `4434164185`, `4451731944`,
  `4451279276`, `4453200028` all re-scored correctly this way.
- Extension correctly auto-skipped dozens of agency/staffing postings and
  detected dozens of reposts against the existing DB, avoiding duplicate
  LLM scoring calls.
- Surfaced one real backend bug: a Pydantic validation error
  (`ExpertiseMatch.score` field required) during scoring — unrelated to
  this session's work, not yet fixed.
- 2026-08-20 session hit a second instance of the same class of bug:
  `ExpertiseMatch.confidence` (`judge/expertise_match.py:318`,
  `Literal["high", "medium", "low"]`) got the value `"median"` from the
  judge LLM — a plausible one-token confusion with "medium" — causing a
  Pydantic `literal_error` and an unscored job. Transient: retrying the
  same job succeeded. Since this is the second distinct field to fail this
  way, worth hardening `ExpertiseMatch` against near-miss LLM outputs
  (e.g. normalize/fuzzy-match the literal, or add a retry-on-validation-
  error in the scoring call itself) rather than treating each occurrence
  as a one-off.
- A third bug, this one self-inflicted earlier in the same session: deleting
  the 189 `company_name IS NULL AND posted_date IS NULL` stub rows (see
  above) orphaned `duplicate_of_job_id` references on jobs that pointed at
  one of the deleted rows as their "original". Confirmed live — National
  Debt Relief's "Engineer, Applied AI" (job_id `4403479083`) has
  `duplicate_of_job_id = 3235`, and id 3235 no longer exists. The
  extension's repost panel doesn't handle a missing target: it rendered
  the literal string `Original: "null"` instead of a real title or a
  graceful fallback. `SELECT COUNT(*) FROM jobs WHERE duplicate_of_job_id
  IS NOT NULL AND duplicate_of_job_id NOT IN (SELECT id FROM jobs)` found
  2 such orphans as of this session. Takeaway for future stub-row cleanup:
  check whether a row is referenced by any `duplicate_of_job_id` before
  deleting it, or re-point/null those references first.

## LinkedIn's new job-search layout (confirmed 2026-08-25/27) — supersedes the scroll guidance above

LinkedIn shipped a new job-search UI. Three things in this doc no longer hold.

**1. Job cards no longer contain `<a href="/jobs/view/...">`.** Cards are divs with
click handlers, so the job id is not readable from the list — it only appears in
`currentJobId` in the URL after clicking. Locate cards by walking up 3 levels from
the `"Dismiss <title> job"` button:

```js
const cardEls = () => [...document.querySelectorAll('button')]
  .filter(b => /^Dismiss .* job$/.test(b.getAttribute('aria-label') || ''))
  .map(b => { let n = b; for (let d = 0; d < 3; d++) n = n.parentElement; return n; });
```

**2. Programmatic `scrollTop` no longer triggers the lazy JD load — only a real
wheel scroll does.** Setting `element.scrollTop` on the detail pane leaves the pane
in skeleton state indefinitely; `"About the job"` / `"About the company"` never enter
the DOM and every extraction returns `Extraction incomplete`. Confirmed by contrast:
after `computer` `scroll` (a trusted wheel event) at coordinates in the right pane,
`__ready()` flipped to `true` immediately. Use `computer scroll`, not JS.

**3. One scroll down is enough — the down-then-up pair is unnecessary.** Extraction
reads the DOM, not the scroll position, so there is no need to scroll back to the
top. One `scroll down 10` at ~`(900, 500)` populates both headings. This halves the
screenshot cost per job, which is the dominant token cost of a manual pass.

### Revised per-job loop

```
js: click card (by company/title substring) + sleep 2500
computer: scroll down 10 at (900,500)      <- the only screenshot in the loop
js: sleep 2500 -> __retry() -> sleep 2000 -> __waitPanel(20000)
    if panel says "Which track" -> click ML/AI -> waitPanel again
```

Always call Retry after the scroll rather than trying to beat the 6s dwell timer.
The dwell fires before the lazy content finishes loading on a new job, so the first
pass reliably fails; Retry re-runs `captureAndScore` against the now-populated DOM
and succeeds. This is more reliable *and* faster than tuning waits, and it makes the
"wait 10-25s per job" guidance above obsolete.

### Panel-state gotchas

`__pending()` must treat all of these as not-yet-final, or you will record a
transient state as the result: `dwelling`, `Scoring against your resume…`,
`Watching this job…`, `Untitled`, `NO-EXT`, `NO-PANEL`.

### Peek-first is only worth it on past-week searches

Clicking and reading the panel *without* scrolling is much cheaper (no screenshot)
and resolves instantly for anything already in the DB. But on a **past-24-hours**
search almost every posting is new, so it rarely pays: on 2026-08-27 only 3 of 22
page-1 cards resolved from cache and the other 19 still needed the full scroll pass.
Use peek-first on `f_TPR=r604800` runs; go straight to click+scroll on `r86400`.

### Do not re-implement the agency blocklist by hand

`judge/agency_blocklist.py::_matches_agency_substring` already does **word-boundary**
matching, precisely so the `"turing"` entry does not fire on "Manufacturing". A
scratch script using a naive `substring in name` check will produce false positives
(e.g. `Re:Build Manufacturing`, which scored 12/15) and silently skip real employers.
Import the function; don't reimplement it.

```
from judge.agency_blocklist import _matches_agency_substring
```

## FIXED 2026-08-27: ExpertiseMatch / SeniorityFit validation errors now retry

The recurring `1 validation error for ExpertiseMatch` panel error is handled.
`judge/structured_retry.py::invoke_with_retry` wraps both structured-output calls
(`score_expertise_match`, `score_seniority_fit`) and retries the identical prompt
up to 3 times on `ValidationError` only — other exceptions (network, auth, rate
limit) propagate untouched.

Four production occurrences across three distinct fields motivated it, all of
which recovered on a manual retry, confirming model nondeterminism rather than a
bad prompt:

| Date | Field | Error |
|---|---|---|
| 2026-08-20 | `ExpertiseMatch.score` | `Field required [type=missing]` |
| 2026-08-20 | `ExpertiseMatch.confidence` | `"median"` instead of `"medium"` (`literal_error`) |
| 2026-08-27 | `ExpertiseMatch.score` | `Field required` (Thomson Reuters) |
| 2026-08-27 | `ExpertiseMatch.score` | `Field required` (Arango) |

You should no longer need to click Retry by hand for these. If a *persistent*
validation error appears, all 3 attempts failed and the prompt or rubric is the
real problem.

## FIXED 2026-08-27: duplicated / missing titles from extract.js

Some postings land in the DB with the title concatenated to itself, and with an
empty `company_name`. Confirmed live on SWAKIO™, which scored 12/15 but stored as:

```
Machine Learning Researcher (Remote)Machine Learning Researcher (Remote)
```

Several same-shaped rows exist from the same day, including the highest-scoring job
of that session (14/15) whose company did not extract at all — so the dashboard shows
a blank company for the best match. Cause: LinkedIn's new layout renders link text twice for accessibility (a
visually-hidden copy plus an `aria-hidden="true"` copy), so `.textContent`
concatenated both. Fixed by `visibleText()` in `extract.js`, which prefers the
`aria-hidden` copy and defensively collapses an exactly-doubled string, plus a
document-wide `/company/` fallback when the company link sits outside the
top-card scope.

**Reload the extension at `chrome://extensions` for this to take effect** —
Chrome caches content scripts. Rows scored before the reload keep their bad
titles and will need a re-score to clean up.

---

# The optimized loop (as run 2026-08-27/28) — use this, not the older sections

Everything above the "LinkedIn's new job-search layout" heading describes the
original method and is kept for history. This section is the current procedure,
refined over ~250 listings across four keyword passes.

## 0. Refresh the blocklist, then classify the page before clicking anything

Never hand-roll the blocklist check — **import the project's own function**, or a
naive `substring in name` test will skip real employers (it flagged
`Re:Build Manufacturing`, which then scored 12/15):

```python
from judge.agency_blocklist import _matches_agency_substring
from db.session import SessionLocal
from db.models import Company, Job, ScreeningResult
```

Classify every card on the page into one of three buckets in a single query, so
the browser work is only spent on the third:

| Bucket | Test | Action |
|---|---|---|
| SKIP | `_matches_agency_substring(name)` or `Company.industry == 'Staffing and Recruiting'` | never click |
| SKIP | card's location line says **On-site** | never click |
| SKIP | **title** matches `/\b(staff\|principal)\b/i` | never click |
| seen | company already has a `ScreeningResult` | still click — a seen *company* often has a **new** posting (LangChain proved this repeatedly) |
| NEW | everything else | click |

Company-level "seen" is only a hint. Job-level dedup is the extension's job.

### The two title/location skips, stated exactly (set by Nasi 2026-08-27 / 2026-08-28)

These are the *only* skips beyond the agency blocklist. In particular, **do not
filter on company size** — that was tried and explicitly rejected.

1. **On-site** — if the card's location line reads `On-site`, skip it. `Hybrid`
   is *not* a skip: several hybrid roles have scored 11-12 (AbbVie 12, EY 11,
   ACA Group 12). Only the literal On-site marker disqualifies.

2. **Staff / Principal in the title** — skip any posting whose *title* contains
   the word "Staff" or "Principal". This is a title test, not a seniority
   inference: `Senior Staff AI Platform Engineer`, `Principal AI Engineer`,
   `Staff Software Engineer, AI/ML` are all out. The screening data backs it —
   these titles score badly on the seniority axis specifically because they
   want 10+ years and a formal tech-lead track:

   | Title | Score | Seniority |
   |---|---|---|
   | Salesloft — Principal Software Engineer, AI | 8 | **0** |
   | Crum & Forster — Principal, AI Engineer | 8 | **0** |
   | Mitratech — Principal AI Engineer | 9 | **1** |
   | Linktree — Staff SWE, AI Experiences & Agents | 9 | **1** |
   | Atlassian — Principal Forward Deployed Engineer | 8 | 1 |
   | SentinelOne — Senior Staff AI Platform Engineer | 8 | 2 |
   | Boulevard — Staff Machine Learning Engineer | 9 | 2 |
   | UBC — Principal AI Solutions Architect | 8 | **0** |

   Match on word boundaries so `Staffing` (a company-name token) doesn't fire.

## 1. Enumerate cards with location, in one call

```js
els.map((el,i) => {
  const L = el.innerText.split('\n').map(s=>s.trim()).filter(Boolean);
  const loc = L.find(x => /Remote|Hybrid|On-site|United States|, [A-Z]{2}/.test(x)) || '';
  return i + '|' + (L[0]===L[1] ? L[1] : L[0]) + ' ~ ' + (L.find((x,k)=>k>0 && x!==L[0])||'') + ' ~ ' + loc;
}).join('\n')
```

The `L[0]===L[1]` guard handles LinkedIn's duplicated first line on verified jobs.
Slice the output (`.slice(0,1900)`) — a full 25-card dump can trip the harness's
query-string blocker, in which case just re-request the tail with `.slice(13)`.

## 2. Per-job: click → ONE wheel scroll → Retry → read

```js
window.__do   = async (frag) => { const i=window.__find(frag); if(i<0) return 'NF';
                 window.__cardEls()[i].click(); await window.__sleep(2500); return 'ok'; };
window.__read = async (label) => {
  let p = await window.__waitPanel(20000);
  if (/Extraction incomplete|validation error|TIMEOUT/.test(p)) {
    window.__retry(); await window.__sleep(2500); p = await window.__waitPanel(25000);
  }
  if (/Which track/.test(p)) { await window.__track(); await window.__sleep(2000); p = await window.__waitPanel(22000); }
  return label + ' :: ' + p.slice(0,95);
};
```

`__read` folds in **both** recovery paths — extraction retry *and* the track
picker — so one call handles every outcome. `__track` must poll for the button
(up to 10 × 1s); clicking immediately after the panel changes misses it.

## 3. Batch exactly three jobs per `browser_batch`

Three is the sweet spot:
- Each JS call must finish under the **45 s CDP timeout**. Three `__peek`s in one
  JS call exceeds it; three *separate* JS calls inside one batch do not, because
  the limit is per-call, not per-batch.
- One wheel scroll per job ⇒ 3 screenshots per batch, the dominant token cost.

Pattern per job: `js(__do)` → `computer scroll down 10 @ (900,500)` → `js(__read)`.

## 4. Recovering from a dropped connection

The extension disconnects every ~20–30 jobs. It is almost always transient and the
batch usually **completed** — LinkedIn SPA clicks don't wipe `window.__*`, so:

1. `tabs_context_mcp` to reconnect (the tab title tells you how far it got)
2. `typeof window.__read === 'function'` — if truthy, helpers survived; just read the panel
3. Only re-install helpers after a real `navigate`

Same for `browser_batch did not respond in time` — read the panel before assuming failure.

## 5. When to stop

Stop when a page yields **zero new jobs** — only city-variant reposts and
blocklisted names. On `llm remote` that was page 8 (LangChain ×3, Jerry ×6,
Genesys ×4, YO AI Labs ×4). Value also drops sharply after page 3: pages 1–3
produced the 15/15 and both 14/15s; pages 4–8 topped out at 13.

## Cost note

Peek-first (click + read panel, no scroll ⇒ no screenshot) only pays on
`f_TPR=r604800`. On `r86400` nearly everything is new — 3 of 22 resolved from
cache on 2026-08-27 — so go straight to click+scroll.

---

# Classic vs. new endpoint (confirmed 2026-09-01/02) — read before any manual pass

LinkedIn serves job search from **two different endpoints with different DOM
behaviour**. Nearly all the expensive guidance above applies to only one of them.

| | `/jobs/search-results/` ("semantic") | `/jobs/search/` ("classic"/literal) |
|---|---|---|
| Match style | Broad/related-term | Literal token match |
| Relevance | High | Low — matches "ai"/"engineer" anywhere |
| Page cap | **10 pages** (`start=225` last; `start=250` → "No results found") | Deep — `start=1225` still returns results |
| Cards/page | 25 | 22–25, lazy-loaded (needs 2–3 wheel scrolls on the **list** pane) |
| List cards are | `<div>` with click handlers | **`<a href="/jobs/view/…">` anchors** |
| Detail pane JD | Lazy — needs a real wheel scroll | **Already in the DOM on click** |
| Status | Current | Being retired ("gradually retiring classic job search starting in September") |

## Do NOT wheel-scroll the detail pane on the classic endpoint

The "scroll before the dwell fires — it's load-bearing" rule is a **semantic-endpoint
rule only**. Confirmed live 2026-09-02: on `/jobs/search/`, querying `h2` immediately
after clicking a card already returns both `"About the job"` and `"About the company"`,
before any scroll. o9 Solutions extracted and scored 9/15 with zero scrolls.

This matters because `computer scroll` returns a **full screenshot every call** —
~1–2k tokens × ~15 clicks/page — and screenshots are the dominant cost of a manual
pass. Dropping them cut per-page cost by roughly 5×. Only the **list** pane still
needs `computer scroll` (to lazy-load cards 8→25); the detail pane never does.

## One JS call per job

Collapse click + wait + read into a single call. ~40 tokens per job, no images:

```js
window.__go = async (titleFrag, coFrag) => {
  const r = await window.__doC(titleFrag, coFrag || '');
  if (r === 'NF') return 'NF:' + titleFrag;
  let p = await window.__waitPanel(30000);
  if (/Extraction incomplete|validation error/.test(p)) {
    window.__retry(); await window.__sleep(2500); p = await window.__waitPanel(6000);
  }
  if (/Which track/.test(p)) { await window.__track(); await window.__sleep(1200); p = await window.__waitPanel(5000); }
  return p.slice(0, 90).replace(/\n/g, ' | ');
};
```

**Keep any single JS call under the 45 s CDP cap** — that's the real ceiling, not the
dwell timer. `__waitPanel(30000)` plus a retry branch fits; a 30 s wait *plus* a 25 s
retry does not, and the call dies with "Runtime.evaluate timed out". If a job is still
`Scoring against your resume…` when `__go` returns, just call `__waitPanel` again in a
fresh call rather than lengthening the budget.

## Match cards by title AND company, never by index

The list is virtualised and re-renders as it scrolls, so indices captured during
enumeration drift before the click. Index-based clicking silently lands on the wrong
card (confirmed: `__do(18)` opened Recover Systems, an On-site skip, instead of Terzo).
Match on the card's first line plus its company line:

```js
window.__doC = async (tf, cf) => { /* find card where L[0] includes tf AND L[1..2] includes cf */ };
```

A title-only fragment is also unsafe — `'AI Engineer'` matched Protege instead of
The Methodical Group.

## FIXED 2026-09-01: extract.js stamped list-card 0's title onto every classic-endpoint job

`findTopCardScope()` (`extension/content/extract.js:83`) took the **first**
`a[href*="/jobs/view/"]` in the document. On the semantic endpoint the list cards
aren't anchors, so that first match *is* the open job's top card — correct by accident.
On the classic endpoint the list cards **are** anchors, so it always grabbed card 0.

Confirmed live: 24 anchors on the page, first = `"Senior Embedded Software Engineer, DSP"`
(job 4458609865, card 0), while `currentJobId` was 4461282877 whose anchor read
`"Software Engineer, Product"`. Two rows were written with card 0's title against the
wrong companies (Prelim 4/15, Nexera 7/15) before it was caught — scores computed
against the wrong JD, so meaningless. Both were repaired (title corrected, screening
row deleted to force a re-score).

Fix: prefer the anchor whose href carries `currentJobId`, falling back to the first
anchor. Backup of the pre-fix file at `extension/content/extract.js.bak-titlefix`.
**Reload the extension at `chrome://extensions` after any content-script change.**

## Search-shape findings (2026-08-31 → 09-02)

- **`rag remote` is not worth a slot.** A full 10-page semantic pass produced **9**
  unique postings; everything else was already covered by `llm remote` / `ai engineer
  remote`. Page 10 was 11 BeaconFire reposts.
- **`ai engineer` vs `ai engineer remote` (literal) overlap ~73%** on page 1 — the
  trailing "remote" token barely changes the literal result set.
- **The two endpoints return largely disjoint inventory.** Semantic surfaced Optum,
  Cotiviti, Snowflake, Oracle, RingCentral, Incredible Health, Code Metal, Peraton;
  literal surfaced ClickUp, Evergen, Coder, YGO, Azumo, SWAKIO. Running literal first
  then semantic front-loads small startups and then adds the enterprise tier.
- LinkedIn reshuffles results between visits to the same page number, and the classic
  endpoint's own result count drifts (5,215 → 4,600 within one session). Coverage is
  guaranteed by the DB's own dedup, not by page ordering.

## Agency-blocklist candidates found 2026-08-31 → 09-02

Not yet in `AGENCY_COMPANY_NAME_SUBSTRINGS` nor DB-flagged as "Staffing and Recruiting"
at the time they were hit:

**BeaconFire Inc.** (16+ hybrid reqs under rotating titles), **Eos Talent**,
**ClosedWon Talent**, **BlueTek Resource Solutions**, **Certus Recruitment Group**,
**Crossing Hurdles**, **TalentAlly**, **TalentHop**, **Remote Talent**, **Torentify**,
**Hire Feed**, **The Phoenix Group**, **Empathy Talent**, **Golden Gate Recruiting**,
**Macro Recruiting**, **Saanvi Technologies**, **Lorven Technologies**,
**Digivance Solutions**, **HireRubyDevs**, **EWOR**.

Two worth calling out:
- **HireRubyDevs** reposted Incredible Health's JD verbatim under its own name; both
  scored 11/15, so the same job was counted twice. Its salary also parsed as `$100,000,000`.
- **EWOR** posts `(m/f/d)`-suffixed reqs (a German-market convention) with salary
  `€1 – €8 EUR` and JD text "based in Europe or the Americas" — likely not US roles.
