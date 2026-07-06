"""Engine + session factory for the job hunt SQLite DB."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base

DB_PATH = Path(__file__).parent.parent / "data" / "job_hunt.db"
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    """Create all tables that don't already exist. Safe to call every run —
    SQLAlchemy skips tables that are already there."""
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
