"""Engine + session factory for the job hunt SQLite DB."""

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base, Job

DB_PATH = Path(__file__).parent.parent / "data" / "job_hunt.db"
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    """Create all tables that don't already exist. Safe to call every run —
    SQLAlchemy skips tables that are already there. `create_all` only adds
    missing *tables*, not missing *columns* on ones that already exist, so
    small additive column migrations are applied by hand afterward."""
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
    matters here, not layout). The table has never been populated (no
    Settings-page upload flow exists yet), so it's safe to drop and let
    create_all rebuild it with the current column set."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(resume)"))}
        if columns == {"id", "content", "original_filename", "uploaded_at", "updated_at"}:
            return
        conn.execute(text("DROP TABLE IF EXISTS resume"))
        conn.commit()
    Base.metadata.create_all(engine)


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


def get_session() -> Session:
    return SessionLocal()
