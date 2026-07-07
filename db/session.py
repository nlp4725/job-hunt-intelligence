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


def get_session() -> Session:
    return SessionLocal()
