"""Engine + session factory.

Local (the default): the SQLite file data/job_hunt.db, its schema kept current
by init_db()'s hand-written _migrate_* chain. Unchanged by the cloud work.

Cloud: JHI_DATABASE_URL (a Postgres URL) points the same models at Postgres,
whose schema is owned by Alembic (`alembic upgrade head`). Deliberately not the
generic DATABASE_URL, which other tools set and which must never move the local
app off SQLite. Set JHI_DATABASE_URL only in the cloud environment, never in
the repo's .env: the local server loads .env too.
"""

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base, Job

DB_PATH = Path(__file__).parent.parent / "data" / "job_hunt.db"
CLOUD_DATABASE_URL = os.environ.get("JHI_DATABASE_URL")

if CLOUD_DATABASE_URL:
    # Stored datetimes are UTC (db.models.utcnow). Pin the session time zone so
    # they land in timestamp columns unshifted whatever the server's default.
    engine = create_engine(CLOUD_DATABASE_URL, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"})
else:
    DB_PATH.parent.mkdir(exist_ok=True)
    engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    """Create all tables that don't already exist. Safe to call every run —
    SQLAlchemy skips tables that are already there. `create_all` only adds
    missing *tables*, not missing *columns* on ones that already exist, so
    small additive column migrations are applied by hand afterward.

    Local SQLite only: the migrations below are SQLite PRAGMA statements."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError("init_db() manages only the local SQLite schema. For Postgres run `alembic upgrade head`.")
    Base.metadata.create_all(engine)
    _migrate_jobs_detail_fetched()
    _migrate_jobs_is_relevant_column()
    _migrate_companies_research_report()
    _drop_legacy_job_analysis_table()
    _migrate_resume_schema()
    _drop_legacy_judge_results_table()
    _drop_legacy_screen_results_table()
    _migrate_screening_results_total_score()
    _migrate_screening_results_skill_group_matched()
    _migrate_jobs_expired_not_interested()
    _migrate_jobs_repost_count()
    _migrate_screening_results_to_c_product_pm()
    _migrate_jobs_note()
    _migrate_jobs_duplicate_of_job_id()
    _migrate_jobs_workplace_type()
    _migrate_jobs_workplace_type_source()
    _migrate_jobs_applied_at()
    _migrate_companies_ats_fields()
    _migrate_jobs_applied_resume_version()


def _migrate_jobs_detail_fetched() -> None:
    """Adds jobs.detail_fetched if this DB predates it, then backfills
    existing rows to True — every row already in the DB was inserted by the
    old code path, which always wrote full detail (title, raw_text, etc.) at
    insert time, so there are no pre-existing placeholder rows to worry
    about. Only genuinely new placeholder rows created going forward start
    out False."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "detail_fetched" in columns:
            return
        conn.execute(text("ALTER TABLE jobs ADD COLUMN detail_fetched BOOLEAN DEFAULT 0"))
        conn.execute(text("UPDATE jobs SET detail_fetched = 1 WHERE title IS NOT NULL"))
        conn.commit()


def _migrate_jobs_is_relevant_column() -> None:
    """Adds jobs.is_relevant if this DB predates it, defaulting every row to
    True. Filtering by analysis/title_filter.py's curated term list now
    happens upstream, at list-page collection time (see
    run_scrape.filter_relevant_ids) — an off-track job never gets a row at
    all, so every row that does exist keeps this column at its default."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "is_relevant" in columns:
            return
        conn.execute(text("ALTER TABLE jobs ADD COLUMN is_relevant BOOLEAN DEFAULT 1"))
        conn.commit()


def _migrate_companies_research_report() -> None:
    """Adds companies.research_report / research_report_generated_at if this
    DB predates the Company Research Agent — both nullable, so existing rows
    just start out un-researched (cache miss on first company-research run)."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(companies)"))}
        if "research_report" not in columns:
            conn.execute(text("ALTER TABLE companies ADD COLUMN research_report TEXT"))
        if "research_report_generated_at" not in columns:
            conn.execute(text("ALTER TABLE companies ADD COLUMN research_report_generated_at DATETIME"))
        conn.commit()


def _drop_legacy_job_analysis_table() -> None:
    """job_analysis was the pre-Screening-Agent resume-match design (keyword_score/
    semantic_score/overall_score) and was never wired up or populated —
    superseded by screening_results (see docs/adr/0003). Safe to drop outright
    rather than carry as dead schema."""
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS job_analysis"))
        conn.commit()


def _migrate_resume_schema() -> None:
    """resume's shape changed during design (briefly held pdf_data for native
    PDF processing, reverted to text-only — extraction is far cheaper in
    tokens than Claude's native PDF vision processing, and only the content
    matters here, not layout). That drop-and-recreate was safe back when the
    table had never been populated; it now holds real per-track resumes (see
    CONTEXT.md "Resume"), so this only adds the `track` column additively for
    a DB still on the old 5-column shape — it must never drop a populated
    table again."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(resume)"))}
        if not columns or "track" in columns:
            return
        conn.execute(text("ALTER TABLE resume ADD COLUMN track TEXT"))
        conn.commit()


def _drop_legacy_judge_results_table() -> None:
    """judge_results was the old Judge Agent's single-call, 4-dimension
    output table (judge/judge_agent.py, now deleted — see CONTEXT.md
    "Job Judge"). Superseded by screening_results (db.models.ScreeningResult),
    a new table create_all already creates on its own — this just clears
    out the old, differently-shaped table rather than leaving it as dead
    schema. The table was never populated (judge_job() never ran against
    real jobs), so dropping it loses nothing."""
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS judge_results"))
        conn.commit()


def _drop_legacy_screen_results_table() -> None:
    """screen_results was ScreeningResult's table under an earlier name
    (Screen Agent, renamed to Screening Agent) — dropped so the correctly-
    named screening_results table (created by create_all) is the only one
    left. Only ever held one test row (job 188, from stage1_screen.py's
    own smoke test), so nothing real is lost."""
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS screen_results"))
        conn.commit()


def _migrate_screening_results_total_score() -> None:
    """Adds screening_results.total_score if this DB predates it (plain sum
    of skill/seniority/expertise scores, added for dashboard ranking).
    Additive/nullable, so existing rows just start out unscored until the
    next screen_job() run recomputes them."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(screening_results)"))}
        if not columns or "total_score" in columns:
            return
        conn.execute(text("ALTER TABLE screening_results ADD COLUMN total_score INTEGER"))
        conn.commit()


def _migrate_screening_results_skill_group_matched() -> None:
    """Adds screening_results.skill_group_matched if this DB predates it —
    JD skills satisfied via a SKILL_GROUPS sibling rather than a direct
    resume match (see analysis/skill_match.py). Additive/nullable."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(screening_results)"))}
        if not columns or "skill_group_matched" in columns:
            return
        conn.execute(text("ALTER TABLE screening_results ADD COLUMN skill_group_matched JSON"))
        conn.commit()


def _migrate_jobs_expired_not_interested() -> None:
    """Adds jobs.expired / not_interested / not_interested_note if this DB
    predates them — dashboard-only user-marked status, distinct from the
    existing scrape-detected `status` (active/stale) column. Additive."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "expired" not in columns:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN expired BOOLEAN DEFAULT 0"))
        if "not_interested" not in columns:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN not_interested BOOLEAN DEFAULT 0"))
        if "not_interested_note" not in columns:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN not_interested_note TEXT"))
        conn.commit()


def _migrate_jobs_repost_count() -> None:
    """Adds jobs.repost_count if this DB predates it — every existing row
    starts at 0 (repost detection is new, there's no history to backfill)."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "repost_count" in columns:
            return
        conn.execute(text("ALTER TABLE jobs ADD COLUMN repost_count INTEGER DEFAULT 0"))
        conn.commit()


def _migrate_screening_results_to_c_product_pm() -> None:
    """Adds screening_results.to_c_product_pm if this DB predates it —
    PM-track-only flag for a consumer/to-C domain match (D2), derived from
    the already-stored expertise_matched_domains JSON rather than a new LLM
    call. Additive; existing rows default to False until backfilled (see
    judge/screening_run.py's one-time backfill for pre-existing rows)."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(screening_results)"))}
        if not columns or "to_c_product_pm" in columns:
            return
        conn.execute(text("ALTER TABLE screening_results ADD COLUMN to_c_product_pm BOOLEAN DEFAULT 0"))
        conn.commit()


def _migrate_jobs_note() -> None:
    """Adds jobs.note if this DB predates it — general free-text note on any
    job (e.g. interview/assessment tracking), independent of not_interested
    status unlike the existing not_interested_note. Additive/nullable."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "note" in columns:
            return
        conn.execute(text("ALTER TABLE jobs ADD COLUMN note TEXT"))
        conn.commit()


def _migrate_jobs_duplicate_of_job_id() -> None:
    """Adds jobs.duplicate_of_job_id if this DB predates it — nullable
    self-referential FK set at scrape time (analysis/duplicate_detector.py)
    when a same-company job's JD text is a near-exact match to an earlier
    job. Additive; existing rows default to NULL (not deduped) since
    detection only runs going forward, not as a retroactive backfill."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "duplicate_of_job_id" in columns:
            return
        conn.execute(text("ALTER TABLE jobs ADD COLUMN duplicate_of_job_id INTEGER REFERENCES jobs(id)"))
        conn.commit()


def _migrate_jobs_workplace_type() -> None:
    """Adds jobs.workplace_type if this DB predates it — "Remote"/"Hybrid"/
    "On-site" as displayed on the job's own detail page, added for the
    dashboard's remote/on-site filter. Additive/nullable; existing rows stay
    NULL until re-scraped (repost detection will naturally refresh most
    still-open ones), not backfilled retroactively."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "workplace_type" in columns:
            return
        conn.execute(text("ALTER TABLE jobs ADD COLUMN workplace_type TEXT"))
        conn.commit()


def _migrate_jobs_workplace_type_source() -> None:
    """Adds jobs.workplace_type_source — provenance for workplace_type, so a
    value LinkedIn actually displayed is never confused with one inferred
    offline from raw_text by analysis/workplace_from_raw_text.py. Existing
    non-NULL workplace_type rows are stamped "linkedin", since every value
    written before this column existed came from the posting itself."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "workplace_type_source" in columns:
            return
        conn.execute(text("ALTER TABLE jobs ADD COLUMN workplace_type_source TEXT"))
        conn.execute(
            text(
                "UPDATE jobs SET workplace_type_source = 'linkedin' "
                "WHERE workplace_type IS NOT NULL"
            )
        )
        conn.commit()


def _migrate_jobs_applied_at() -> None:
    """Adds jobs.applied_at if this DB predates it — nullable, set going
    forward by PATCH /api/jobs/<id> (backend/app.py) whenever `applied`
    flips to True. Existing applied=True rows stay NULL rather than being
    backfilled with a guessed date — there's no record of when they were
    actually marked applied before this column existed."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "applied_at" in columns:
            return
        conn.execute(text("ALTER TABLE jobs ADD COLUMN applied_at DATETIME"))
        conn.commit()


def _migrate_companies_ats_fields() -> None:
    """Adds companies.ats_provider/ats_slug/ats_checked_at if this DB
    predates them — populated by analysis/ats_detector.py probing public
    Greenhouse/Lever/Ashby/etc. job-board APIs by slug, as a LinkedIn-
    independent way to find a company's own career-page listings. Additive;
    existing rows start NULL (not yet checked) until ats_detector.py runs."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(companies)"))}
        if "ats_provider" in columns:
            return
        conn.execute(text("ALTER TABLE companies ADD COLUMN ats_provider TEXT"))
        conn.execute(text("ALTER TABLE companies ADD COLUMN ats_slug TEXT"))
        conn.execute(text("ALTER TABLE companies ADD COLUMN ats_checked_at DATETIME"))
        conn.commit()


def _migrate_jobs_applied_resume_version() -> None:
    """Adds jobs.applied_resume_version if this DB predates it — nullable,
    set by the browser extension's apply buttons ("Applied w/ v1"/"Applied
    w/ v2") for the August 2026 resume A/B test (see CONTEXT.md "Resume A/B
    Test"). Existing applied=True rows stay NULL — no record of which
    resume version (if any) they were applied with before this existed."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
        if "applied_resume_version" in columns:
            return
        conn.execute(text("ALTER TABLE jobs ADD COLUMN applied_resume_version TEXT"))
        conn.commit()


def get_session() -> Session:
    return SessionLocal()
