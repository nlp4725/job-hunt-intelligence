# Job Hunt Intelligence

Collects ML/AI and product-manager job listings from LinkedIn, stores them in a database, and screens each one against a resume. Every job gets three separate scores (Skill Match, Seniority Fit, Expertise Match) shown on a dashboard.

The local app is a single-user tool. A multi-user cloud version (shared job board, per-user resumes, scores and application tracking) is being built alongside it; see [docs/productization_build_plan.md](docs/productization_build_plan.md).

## How it works

```
LinkedIn ──► Selenium scraper ─┐
                               ├──► save_new_job ──► SQLite / Postgres ──► Screening ──► Dashboard
LinkedIn ──► browser extension ┘     (dedup, skills,                        (3 scores)     (Flask + React)
                                      derived fields)
```

**Collection.** Jobs come in two ways, and both end at `db/job_writer.save_new_job`:
- `scraper/`: a Selenium scraper that runs keyword searches, filters titles for relevance, and fetches each job's detail page.
- `extension/`: a Chrome extension. While you browse LinkedIn, it captures the job you're viewing, sends it to the local API, and shows the scores in a side panel.

**Screening.** `judge/stage1_screen.screen_job()` gives each job three scores. They are kept separate on purpose, not blended into one number:

| Score | How | Range |
|---|---|---|
| **Skill Match** | Deterministic. A taxonomy of ~155 skills (`analysis/skills_extractor.py`) is matched in the resume and in the job description, and the overlap is banded onto 0-5. Adjacent tools count as a match (e.g. AWS for GCP). | 0-5 |
| **Seniority Fit** | LLM rubric (`judge/seniority_fit.py`). Does the role's level of responsibility match the candidate's? | 0-5 |
| **Expertise Match** | LLM rubric (`judge/expertise_match.py`). Is the candidate's domain and capability background an advantage for the role's core problem? | 0-5 |

Both LLM scores use DeepSeek V4 Pro with thinking disabled. It was chosen over Claude Haiku 4.5 on eval results: similar accuracy at a fraction of the cost. Staffing agencies and data-labeling gig platforms are filtered out before screening (`judge/agency_blocklist.py`).

**Dashboard.** `backend/app.py` is a Flask API. It serves a React board (`frontend/`, at `/app/`) and the original single-page board (at `/`).

## Repository layout

| Path | What's there |
|---|---|
| `scraper/` | Selenium LinkedIn scraper and run scripts |
| `extension/` | Chrome extension (content script, side panel, background worker) |
| `backend/` | Flask API and the original dashboard page |
| `frontend/` | React + Vite dashboard |
| `db/` | SQLAlchemy models, session, job writer, Alembic migrations (cloud), cloud copy/seed scripts |
| `judge/` | Screening orchestration, LLM rubrics, agency blocklist, batch screening run |
| `analysis/` | Deterministic parsing and scoring: skills, skill match, title filter, dates, salary, workplace type, duplicates |
| `resume/` | Resume to text (PDF/DOCX/TXT), PII redaction, encrypted storage, ingest |
| `tests_and_eval/` | pytest suites, gold-set evals, LangSmith calibration harnesses |
| `docs/` | Design plans and [architecture decision records](docs/adr/) |

## Setup

Requires Python 3.11+ and Node 20+ (for the frontend).

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cd frontend && npm install && npm run build && cd ..
```

Create a `.env` file in the repo root:

```bash
DEEPSEEK_API_KEY=...        # Seniority Fit and Expertise Match
ANTHROPIC_API_KEY=...       # optional: eval comparisons, company research
LANGSMITH_API_KEY=...       # optional: calibration harnesses
JHI_DATABASE_URL=...        # optional: Postgres URL for the cloud schema; defaults to SQLite at data/job_hunt.db
JHI_RESUME_KEY=...          # cloud only: Fernet key for encrypting stored resumes
```

The SQLite database is created at `data/job_hunt.db` on first run. The resume used for local screening is a row in the `resume` table (one per track).

## Running

```bash
# Dashboard and API on http://127.0.0.1:5050 (React board at /app/)
python -m backend.app

# Scrape LinkedIn (requires a signed-in browser profile; see scraper/setup_chrome_profile.py)
python -m scraper.run_scrape

# Screen every job that has no score yet
python -m judge.screening_run

# Frontend with hot reload on :5173/app/ (proxies /api to Flask)
cd frontend && npm run dev
```

**Extension:** open `chrome://extensions`, turn on Developer mode, choose **Load unpacked**, and select `extension/`. It talks to the local API on port 5050.

**Cloud schema (Postgres):** set `JHI_DATABASE_URL`, then `alembic upgrade head`.

## Tests

```bash
pytest
```

- Postgres-backed tests (`test_cloud_*.py`) are skipped unless `JHI_TEST_POSTGRES_URL` is set.
- Live-API probes and the LangSmith calibration harnesses are excluded in `pytest.ini`. They cost money per run; run them directly when re-calibrating a rubric.
- Extension extraction is tested with jsdom against recorded LinkedIn layouts: `node tests_and_eval/extraction_fixtures.js`.

## Design notes

- [docs/adr/](docs/adr/): architecture decision records
- [docs/productization_build_plan.md](docs/productization_build_plan.md): the multi-user cloud plan
- [docs/resume_jd_skill_pipeline.md](docs/resume_jd_skill_pipeline.md): resume parsing and skill extraction
- [docs/production_data_quality.md](docs/production_data_quality.md): data-quality checks on collected jobs
