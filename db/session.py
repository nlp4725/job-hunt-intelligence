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
    just start out un-researched (cache miss on first Judge Agent run)."""
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(companies)"))}
        if "research_report" not in columns:
            conn.execute(text("ALTER TABLE companies ADD COLUMN research_report TEXT"))
        if "research_report_generated_at" not in columns:
            conn.execute(text("ALTER TABLE companies ADD COLUMN research_report_generated_at DATETIME"))
        conn.commit()


def _drop_legacy_job_analysis_table() -> None:
    """job_analysis was the pre-Judge-Agent resume-match design (keyword_score/
    semantic_score/overall_score) and was never wired up or populated —
    superseded by judge_results (see docs/adr/0003). Safe to drop outright
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


def get_session() -> Session:
    return SessionLocal()
