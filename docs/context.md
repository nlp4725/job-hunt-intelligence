# Job Hunt Intelligence — Build Plan

## Context

The user wants an automated system to help their job search: scrape new ML/AI/PM listings from LinkedIn daily, store them with metadata, enrich company data, surface everything on a dashboard, and (later) score resume match. This is a greenfield project — the working directory is currently empty.

Key decisions already made with the user:
- **Sources/keywords**: LinkedIn Jobs; keywords "machine learning", "AI engineer", "AI scientist", "Product manager"
- **Scrape cadence**: 3x/day, run **locally** (not cloud) via macOS `launchd`, using Selenium + BeautifulSoup with the user's real Chrome session (avoids LinkedIn's automated-login detection — the main risk with automating LinkedIn access)
- **Stack**: Python + FastAPI + SQLite + React
- **Company research**: Claude API (web search tool) does the lookup — homepage, industry, size, brief summary
- **Resume match score**: on-demand per-job "Analyze" button (keyword overlap + Claude semantic/gap analysis), resume provided once via a settings page
- **Industry trends / personal advantage & gap**: pure aggregation over already-stored data, no new "memory" system
- **Chat**: persistent, tool-using Claude agent that can query the jobs DB to answer free-form questions ("did I already apply to X?")

## Status as of 2026-07-06

**Built and validated against real LinkedIn data:**
- Scraper (`scraper/linkedin_scraper.py`, `scraper/run_scrape.py`) — list scraping with title-relevance filtering (see below), unbounded pagination, per-job detail fetch (title/company/location/industry/size/raw_text/salary/posted-date/applicant-stats), block detection, human-like reading pace, daily (2-phase) + initial-backfill run modes
- Title-relevance filtering (`analysis/title_filter.py`) — curated per-track term list + word-boundary regex; now wired into `run_scrape.py` and applied at list-page time, before a job ever gets a placeholder row or a full detail fetch (previously built but unused)
- Skills extractor (`analysis/skills_extractor.py`) — 155 skills, 18 categories
- DB layer (`db/models.py`, `db/session.py`) — 7 tables, tested end-to-end (scrape → dedup → title filter → insert job → create/update company → extract skills → insert job_skills)

**Not yet built:**
- `company_analyzer.py`'s Claude fallback (LinkedIn-first path is done; the About-page richer fields — homepage_url, summary, headquarters, founded, specialties — aren't wired into `run_scrape.py` yet, only industry/size are)
- FastAPI backend, React frontend, resume settings, resume-match analysis wiring, dashboard views, chat agent, launchd scheduling
- A full, real `run_scrape.py` execution hasn't been run yet (only capped/manual tests so far) — `scrape_runs` table is still empty
- A third track for general software roles (software engineer/developer/data engineer) — discussed, not yet added; would need its own search keyword(s) and title-filter term list rather than folding into `ml_ai`

## Why these specific technical calls

- **One SQLite file, multiple tables** (not two separate DB files) — `jobs`, `companies`, `skills` junction, `scrape_runs`. This lets a job listing join to its company record and lets the skills dashboard aggregate across both. Framed to the user as "job DB" + "company DB" conceptually, but physically one file for referential integrity.
- **Dedup via job URL**: since 3x/day scraping will re-see the same listings, `job_url` is the unique key; re-seeing a listing updates `last_seen_at` rather than inserting a duplicate row.
- **Skill extraction via curated keyword list**, not an LLM call per job. Free, deterministic, instant, and good enough for a "what skills are trending" dashboard. Built: 155 skills across 18 categories (`analysis/skills_extractor.py`) — programming languages, ML/DL frameworks, LLM/GenAI, Agentic AI, Enterprise LLM platforms, AI Evaluation & Safety, computer vision/NLP, data science, classical ML models, data engineering, databases, cloud/infra, web scraping, MLOps, version control, product management, education, recommendation systems. Each skill maps to a `SKILL_CATEGORIES` theme (e.g. "Agentic AI") so theme-level stats ("% of jobs mentioning anything agentic") can count each job once even if it matched several tags in that theme, rather than double-counting. A startup-time consistency check keeps the taxonomy and category groupings from silently drifting apart. LLM-based extraction remains a documented future upgrade path, not needed to hit the goal.
- **Company research via Claude + web search tool** (`web_search_20260209`, per the claude-api skill) — the model looks up small/startup companies it doesn't know from training data, not just well-known ones. Runs once per *new* company name (cached in the `companies` table), not per job. Uses `claude-opus-4-8` per the account's default model policy; the user can swap to `claude-haiku-4-5` later purely to cut cost, since this runs unattended and companies accumulate over time.
- **launchd, not a task queue** — one batch script, 3 fixed times a day, each run takes a few minutes. Celery/RQ-style workers exist for concurrent/long-running job processing, which doesn't apply here.

## Repo layout

```
job_hunt_intelligence/
  data/
    job_hunt.db                 # sqlite (gitignored) — built, 7 tables live
  scraper/
    .chrome-profile/             # dedicated Chrome profile dir (gitignored) — logged into LinkedIn once via setup_chrome_profile.py
    setup_chrome_profile.py      # one-time manual LinkedIn login, saves session to .chrome-profile/
    linkedin_scraper.py          # built: list scrape (job_id + title per card), two-pane detail fetch, pagination, block detection, human-like pacing
    run_scrape.py                # built: real orchestrator — daily (2-phase) + initial-backfill paths, per-page dedup + title filter + detail fetch + skill extraction + scrape_runs logging
  analysis/
    company_analyzer.py          # Claude + web_search -> homepage/industry/size/summary, upserts companies table
    skills_extractor.py          # curated keyword list + regex matching against raw_text -> job_skills rows
    title_filter.py              # curated per-track term list + regex against list-page title -> drops off-track jobs before any DB write or detail fetch
    resume_analyzer.py           # keyword score (reuses skills_extractor) + Claude semantic/gap call
  db/
    models.py                    # SQLAlchemy models: Job, Company, Skill, JobSkill, ScrapeRun, Resume, JobAnalysis, ChatMessage
    session.py                   # engine/session factory
  backend/
    main.py                      # FastAPI app
    routers/jobs.py               # GET/PATCH jobs (list, filter, mark applied)
    routers/companies.py          # GET companies, trigger re-analysis
    routers/dashboard.py          # skills-frequency, trends, gap/advantage aggregation endpoints
    routers/analysis.py           # POST/GET job analysis (resume match)
    routers/settings.py           # resume upload/get (paste text or PDF upload)
    routers/chat.py                # POST /chat/message, GET /chat/history
    chat_tools.py                  # tool definitions + implementations for the chat agent
  frontend/                      # Vite + React
    src/pages/JobsTable.tsx        # gains an Analyze button + inline analysis panel
    src/pages/CompanyDetail.tsx
    src/pages/SkillsDashboard.tsx  # gains trends-over-time + gap/advantage panel
    src/pages/Settings.tsx         # resume paste/upload
    src/pages/Chat.tsx             # persistent chat UI
  scheduler/
    com.jobhunt.scraper.plist    # launchd job, 3x/day (e.g. 8am/1pm/6pm) with StartCalendarInterval
    install.sh                   # copies plist to ~/Library/LaunchAgents, loads it
  requirements.txt
  .env.example                   # ANTHROPIC_API_KEY
  .gitignore
```

## Database schema

**Built and validated** (SQLite, `data/job_hunt.db`, via SQLAlchemy — `db/models.py` + `db/session.py`). One file, 7 tables — including a `track` column on `jobs` (`ml_ai` vs `pm`) instead of two physically separate DB files, so cross-track aggregation (skills dashboard, shared company records) stays possible. Decided explicitly with the user over two physically-separate-DB files.

**jobs**
`id, job_id (LinkedIn's own ID, unique — the real dedup key, not the URL), url, title, company_name, company_id (FK), location, keyword_matched, track (ml_ai/pm), raw_text, salary_text, salary_min/salary_max (nullable, future LLM extraction), posted_date, applicant_stats, match_score (nullable, future resume matching), applied (bool, default false), status (active/stale), first_seen_at, last_seen_at`

**companies**
`id, name (unique), homepage_url, industry, size, summary, analyzed_at` — currently only `industry`/`size` get populated automatically (from the job's own two-pane view, see Scraper design below); `homepage_url`/`summary`/richer fields require a separate visit to the company's LinkedIn About page or the Claude fallback, not yet wired into `run_scrape.py`.

**job_skills** (junction: `job_id, skill_name`, unique constraint on the pair) — powers the dashboard aggregation (count of jobs mentioning each skill, filterable by keyword/company)

**scrape_runs**
`id, run_at, keyword, track, num_found, num_new, status, error_message` — lets the dashboard show scrape health. Not yet populated by a real run — testing so far has called the scraper functions directly, not the full `run_scrape.py` orchestrator.

**resume**
`id, content (extracted text), original_filename (nullable), uploaded_at, updated_at` — single active resume; a new upload adds a row and becomes "current" (most recent by `updated_at`)

**job_analysis**
`id, job_id (FK, unique — one row per job, re-analyzing overwrites), keyword_score, semantic_score, overall_score, matched_skills (json), gap_skills (json), narrative (text), analyzed_at`

**chat_messages**
`id, role (user/assistant), content, created_at` — one continuous log, no conversation-threading needed

## Scraper design — built and validated (`scraper/linkedin_scraper.py`, `scraper/run_scrape.py`)

Substantially revised from the original plan after direct investigation of LinkedIn's actual rendering behavior:

- Selenium launches Chrome with `--user-data-dir` pointed at a **dedicated Chrome profile** (`scraper/.chrome-profile/`, not the user's daily-driver profile) logged into LinkedIn manually once via `setup_chrome_profile.py`. Google SSO doesn't work in an automated browser (Google blocks it as a security measure) — login with a LinkedIn email+password instead.
- **List step reads job_id + title per card, and does need scrolling**: LinkedIn renders all 25 `li[data-occludable-job-id]` wrappers in the DOM immediately on page load, but the list is virtualized — a card's title text only renders once that card has scrolled near the viewport at least once (confirmed by direct inspection: the first ~7 cards have a title on load, the rest come back with an empty `<strong>` until scrolled to; once rendered, a title stays populated even after scrolling past it). `get_job_cards_on_page()` scrolls the whole list to the bottom (`simulate_list_browsing()` — the real scrollable element is the `<ul>`'s parent div, not `div.scaffold-layout__list` itself, since Ember gives it a hashed/unstable class name each session, so it's located structurally rather than by class) before parsing job_id + title straight off `div.scaffold-layout__list-detail-inner div.scaffold-layout__list ul`.
- **Title-relevance filtering happens right after list-page collection, before any DB write or detail fetch** (`analysis/title_filter.py` + `run_scrape.py`'s `filter_relevant_ids()`): LinkedIn's `keywords=` search matches a posting's whole text, not just its title, so a "machine learning" search also returns plenty of off-track roles (accounting, sales, unrelated engineering, etc.) that only mention the keyword in passing. A job whose title doesn't match its track's curated term list gets skipped entirely — no placeholder row, no detail fetch. A previously-saved placeholder (from an interrupted earlier run) that turns out irrelevant on a later pass gets deleted outright, rather than retried forever.
- **Pagination**: LinkedIn's `&start=N` parameter increments by a confirmed, consistent 25 per page (verified `start=75` returns a genuinely different result set, not skipped). Both the daily and backfill paths page until "paged past everything new" is detected (2 consecutive pages with nothing needing detail) or a hard page cap is hit (`MAX_PAGES_DAILY=60`, `MAX_PAGES_BACKFILL=200`) — LinkedIn doesn't return a genuinely empty page once you've paged past its real result set, it just keeps serving repeats.
- **Two run modes**: routine **daily** run (`run_daily()`, `TIME_RANGE_DAY`) splits into two passes — phase 1 (`collect_new_ids()`) pages through all 4 keywords doing dedup + title-filtering only (fast, no per-job detail fetch, so LinkedIn's live feed has much less time to shift underneath the pagination), saving a bare placeholder row (`detail_fetched=False`) for every relevant brand-new id; phase 2 then fetches full detail for everything phase 1 found relevant, one job at a time. One-time **initial backfill** (`process_keyword_backfill()`, `--initial` flag, `TIME_RANGE_MONTH`) is simpler and interleaved — dedup + filter + full detail fetch happen immediately after each page loads, since the 30-day pool is large enough that the two-phase split isn't worth the extra bookkeeping for a single one-off run.
- **Per-job detail fetch** reuses the **search-results two-pane view** (`&currentJobId=<id>` appended to the same search URL) rather than the standalone `/jobs/view/<id>/` page — the standalone page renders with hashed, unstable CSS classes (confirmed by direct investigation), while the two-pane view uses stable, semantic ones. One page load gets: `h1` (title), `div.job-details-jobs-unified-top-card__company-name` (company), the tertiary-description spans (location, posted-date text, applicant-count text — matched by keyword content like " ago"/"applicant", not fixed position), `div#job-details` (full raw JD text), `section.jobs-company` mini-card (industry, company size — free, no LLM), and `div.job-details-fit-level-preferences` buttons (salary, matched against a regex since not every job shows one).
- **Human-like pacing, not just fixed delays**: `simulate_reading()` scrolls the *actual* nested scrollable container (`div.jobs-search__job-details--wrapper` — confirmed via inspection that the outer `window` doesn't scroll this pane) in small random increments over a randomized 3-15s dwell per new job, including occasional scroll-**up** moves (35% of the time) so the pattern isn't monotonically forward like a script's would be. `simulate_list_browsing()` uses the same randomized-movement idea for the list-scroll pass, just biased more strongly downward since its goal is full coverage of the page's cards rather than idle browsing.
- **Block detection**: `LinkedInBlockedError` / `check_not_blocked()` checks the landed-on URL for `/checkpoint/`, `/authwall`, or `/login` and stops the entire run immediately — no retry, since retrying into a challenge is what actually gets accounts flagged.
- **Dedup**: `job_id` (LinkedIn's own ID) is the real key, not the URL (the URL's tracking query params differ per page load even for the same job). Each page's 25 IDs are checked against the DB in **one batch query** (`dedup_page()`, `Job.job_id.in_(page_ids)`), not 25 individual ones; already-known jobs (`detail_fetched=True`) just get `last_seen_at` bumped. The remainder (genuinely new, or a placeholder left by an interrupted run) goes through the title filter next, and only what survives that gets a placeholder row and/or the full detail fetch + skill extraction.
- Skills are extracted from `raw_text` **immediately**, same pass as the DB insert (free, instant — see `analysis/skills_extractor.py`); a progress line prints every 5 new jobs saved.
- Each keyword's run logs one `scrape_runs` row; a block on one keyword stops the whole run (a block reflects the LinkedIn session's state, not something isolated to one keyword).

## launchd setup

Three `StartCalendarInterval` entries (e.g. 8:00, 13:00, 18:00) in the plist, each invoking `run_scrape.py` inside the project's venv. `install.sh` copies the plist to `~/Library/LaunchAgents` and `launchctl load`s it. If the Mac is asleep at fire time, launchd runs it on next wake — noted as an accepted tradeoff (no `caffeinate` requirement imposed).

## Company analysis flow

**Revised after investigation**: LinkedIn's own company "About" page (`linkedin.com/company/{slug}/about/`, slug already captured from the job card) reliably exposes Industry, Company size, Website, Headquarters, Founded, Specialties, and an Overview description via a stable structure — `section.org-about-module__margin-bottom` containing a `dl.overflow-hidden` of `dt`/`dd` pairs (dt's `<h3>` text is the label, the following `dd` is the value). Confirmed working across three real companies of very different sizes (10,001+, 501-1,000, and 2-10 employees).

Triggered for any `company_name` not yet in `companies`:
1. **Primary (free, deterministic)**: scrape the LinkedIn About page directly using the selectors above — covers homepage_url, industry, size, and a summary (LinkedIn's own Overview text), no API cost.
2. **Fallback (Claude + `web_search_20260209`)**: only invoked if the About page is missing or its fields come back mostly empty (e.g. a very new company with an incomplete LinkedIn profile) — same prompt/schema as originally planned, parses response as JSON, upserts into `companies`. Requires `ANTHROPIC_API_KEY` in `.env`.

This flips company enrichment from "always costs an LLM call" to "free for the large majority of companies, LLM only as a fallback" — a meaningful cost reduction over the original all-LLM design.

## Resume analysis workflow (`analysis/resume_analyzer.py`)

1. **Keyword score (instant, deterministic)**: reuse `skills_extractor.py` against the resume's stored text and compare to the job's already-extracted `job_skills` rows — simple overlap ratio. Returned immediately on click.
2. **Semantic score + gap analysis (one Claude call)**: `claude-opus-4-8`, given the full resume text + full job `raw_text` + the keyword score as context. Uses `output_config.format` (structured outputs — no `web_search` needed, this is pure reasoning over provided text) to return `semantic_score`, refined `matched_skills`, `gap_skills` (each with a one-line reason), `overall_score`, and a short narrative.
3. Upsert into `job_analysis`.

## Trends & gap/advantage views (pure aggregation, no new "memory" system)

- `GET /dashboard/trends` — skill mention frequency grouped by week/month over `jobs.first_seen_at` joined to `job_skills`.
- `GET /dashboard/gaps` — aggregates across all `job_analysis` rows: skills most often in `gap_skills` ("my gap") vs. most often in `matched_skills` on higher-scoring jobs ("my advantage"). Emerges naturally once enough jobs are analyzed.

## Chat (the agentic piece)

- **Tools** (`backend/chat_tools.py`), thin wrappers over DB queries, exposed to Claude as function-calling tools: `search_jobs`, `get_job_details`, `check_applied`, `get_skill_gap_summary`, `get_hiring_trends`.
- **Endpoint** `POST /chat/message` — loads history from `chat_messages`, runs a manual tool-use loop with `claude-opus-4-8` (call → execute matching tool on `stop_reason == "tool_use"` → feed `tool_result` back → repeat until `end_turn`), persists user + assistant messages. `GET /chat/history` for page load. No `web_search` — only reads the user's own DB.

## Backend (FastAPI)

- `GET /jobs` — list/filter (keyword, applied status, company, date range)
- `PATCH /jobs/{id}` — toggle `applied`
- `GET /companies` / `GET /companies/{id}`
- `GET /dashboard/skills` — top-N skills mentioned, with counts, optionally grouped by keyword or company industry
- `GET /dashboard/trends` — skill frequency over time
- `GET /dashboard/gaps` — personal advantage/gap aggregation
- `GET /dashboard/scrape-health` — recent `scrape_runs` summary
- `POST /jobs/{id}/analyze` / `GET /jobs/{id}/analysis` — resume match analysis
- `POST /settings/resume` / `GET /settings/resume` — resume upload/retrieval
- `POST /chat/message` / `GET /chat/history` — chat agent

## Frontend (React + Vite)

- **Jobs table**: sortable/filterable list (title, company, location, salary, applied checkbox, link out, **Analyze button** with inline keyword/semantic score + gap panel)
- **Company detail**: industry/size/summary + list of their open jobs
- **Skills dashboard**: bar chart of most-mentioned skills, **trends-over-time view**, **gap/advantage panel** (reuse the `dataviz` skill's palette/chart guidance)
- **Settings**: paste or upload resume (PDF text-extracted via `pypdf`/`pdfplumber`)
- **Chat**: scrollable persisted history + input box

## Explicitly deferred (not built now)

- LLM-based skill extraction for jobs — keyword-list approach only for now (resume matching still uses the same curated-list extractor for its keyword-score component).
- Job theme/industry/compensation classification via structured-output Claude call (theme: traditional/agentic, focus_area, industry, salary_min/max) — designed conceptually, not implemented yet. Cheapest path is folding it into the on-demand `resume_analyzer` call (no extra cost for analyzed jobs) rather than a separate automatic per-scrape call.
- Trained ML "match vs. not match" classifier using structured features (skills one-hot vector, theme/industry/focus_area, salary) as input — not raw JD text or job title alone (too high-dimensional / too weak a signal respectively for the small labeled dataset a personal job search will realistically produce). Revisit once enough applied/rejected history has accumulated in the `jobs` table; until then the zero-shot Claude resume analysis covers this need without requiring training data.

## Verification plan

1. Manually log into LinkedIn in the dedicated Chrome profile once.
2. Run `run_scrape.py` by hand for one keyword, confirm rows land in `jobs`, a new company triggers `company_analyzer` and populates `companies`, and `job_skills` gets populated.
3. Start FastAPI (`uvicorn backend.main:app --reload`) and hit `/jobs`, `/companies`, `/dashboard/skills` to confirm data flows through.
4. Start the Vite dev server, confirm the pages render real data from the API.
5. Install the launchd plist, verify with `launchctl list | grep jobhunt` and check `scrape_runs` after the next scheduled fire time.
6. Paste a sample resume in Settings, confirm it's stored and retrievable.
7. Click "Analyze" on a scraped job — confirm keyword score appears instantly and the semantic/gap result lands in `job_analysis` shortly after.
8. Hit `/dashboard/trends` and `/dashboard/gaps` after a few jobs are analyzed — confirm sensible aggregation.
9. In the Chat page, ask "did I apply to <company>?" and confirm the tool-use loop calls `check_applied` and answers correctly; reload the page and confirm history persists.
