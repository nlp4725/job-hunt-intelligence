"""Cloud database alongside local (docs/productization_build_plan.md, phase 1).

Local must not change: with JHI_DATABASE_URL unset, db.session uses the same
SQLite file and init_db() as before. JHI_DATABASE_URL — deliberately not the
generic DATABASE_URL, which other tools set — points the same code at
Postgres, whose schema is owned by Alembic.

The Postgres tests need a disposable database they may wipe, e.g.
    JHI_TEST_POSTGRES_URL=postgresql+psycopg://jhi@localhost:5433/jhi_test
and skip without it.
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from db.job_writer import save_new_job
from db.models import Base, Company, Job, ScreeningResult
from tests_and_eval.test_jd_normalize import _detail

REPO = Path(__file__).resolve().parent.parent
PG_URL = os.environ.get("JHI_TEST_POSTGRES_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="set JHI_TEST_POSTGRES_URL to a disposable Postgres database")
CLOUD_URL = "postgresql+psycopg://jhi@localhost:5433/jhi_cloud"


def _run(code: str, **env) -> subprocess.CompletedProcess:
    """Import db.session in a fresh interpreter, so each case sees only its own env."""
    clean = {k: v for k, v in os.environ.items() if k not in ("JHI_DATABASE_URL", "DATABASE_URL")}
    clean.update(env)
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, env=clean, capture_output=True, text=True)


def _engine_url(**env) -> str:
    result = _run("import db.session as s; print(s.engine.url.render_as_string(hide_password=True))", **env)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class TestLocalStaysSqlite:
    def test_default_is_the_local_sqlite_file(self):
        assert _engine_url() == f"sqlite:///{REPO / 'data' / 'job_hunt.db'}"

    def test_generic_database_url_is_ignored(self):
        """Other tools set DATABASE_URL; it must never move the local app off SQLite."""
        assert _engine_url(DATABASE_URL="postgresql+psycopg://someone@localhost/other").startswith("sqlite:///")


class TestCloudDatabaseUrl:
    def test_jhi_database_url_selects_postgres(self):
        assert _engine_url(JHI_DATABASE_URL=CLOUD_URL) == CLOUD_URL

    def test_init_db_refuses_postgres(self):
        """Postgres schema comes only from Alembic; the SQLite _migrate_* chain
        would run PRAGMA statements against it."""
        result = _run("import db.session as s; s.init_db()", JHI_DATABASE_URL=CLOUD_URL)
        assert result.returncode != 0
        assert "alembic upgrade head" in result.stderr


@pytest.fixture
def pg_engine():
    engine = create_engine(PG_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()


def _upgrade(url: str):
    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return config


@needs_pg
class TestAlembic:
    def test_upgrade_creates_every_model_table(self, pg_engine):
        _upgrade(PG_URL)
        assert set(Base.metadata.tables) <= set(inspect(pg_engine).get_table_names())

    def test_models_and_migrations_agree(self, pg_engine):
        """A model change without a migration fails here, not in the cloud."""
        from alembic import command

        command.check(_upgrade(PG_URL))


JD = "Build LLM systems with Python and SQL on AWS. " * 10
SCREENED_AT = datetime(2026, 9, 15, 17, 13, tzinfo=timezone.utc)


def _local_db(tmp_path: Path) -> Path:
    """A small local SQLite DB written by the real capture path."""
    path = tmp_path / "local.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    original = save_new_job(session, "llm remote", "ml_ai", "100", _detail(JD))
    save_new_job(session, "llm remote", "ml_ai", "101", _detail(JD))   # a repost: duplicate_of_job_id -> original
    session.add(ScreeningResult(job_id=original.id, skill_score=3, skill_matched=["Python", "SQL"],
                                total_score=11, screened_at=SCREENED_AT))
    session.commit()
    session.close()
    engine.dispose()
    return path


@needs_pg
class TestCopySqliteToPostgres:
    def test_every_row_arrives_with_types_intact(self, pg_engine, tmp_path):
        from db.copy_to_cloud import copy_sqlite_to_postgres

        _upgrade(PG_URL)
        result = copy_sqlite_to_postgres(_local_db(tmp_path), PG_URL)

        counts = result.copied
        assert counts["jobs"] == 2 and counts["screening_results"] == 1 and counts["job_skills"] >= 3
        assert result.skipped == {}
        session = sessionmaker(bind=pg_engine)()
        jobs = {job.job_id: job for job in session.query(Job)}
        assert jobs["101"].duplicate_of_job_id == jobs["100"].id
        result = session.query(ScreeningResult).one()
        assert result.skill_matched == ["Python", "SQL"]
        assert result.screened_at.replace(tzinfo=None) == SCREENED_AT.replace(tzinfo=None)
        session.close()

    def test_new_rows_get_fresh_ids_after_the_copy(self, pg_engine, tmp_path):
        """Copied rows keep their ids, so each id sequence must continue past them."""
        from db.copy_to_cloud import copy_sqlite_to_postgres

        _upgrade(PG_URL)
        copy_sqlite_to_postgres(_local_db(tmp_path), PG_URL)
        session = sessionmaker(bind=pg_engine)()
        session.add(Company(name="NewCo"))
        session.commit()
        assert session.query(Company).count() == 2
        session.close()

    def test_refuses_a_target_that_already_has_rows(self, pg_engine, tmp_path):
        from db.copy_to_cloud import copy_sqlite_to_postgres

        _upgrade(PG_URL)
        source = _local_db(tmp_path)
        copy_sqlite_to_postgres(source, PG_URL)
        with pytest.raises(RuntimeError, match="not empty"):
            copy_sqlite_to_postgres(source, PG_URL)

    def test_the_local_file_is_never_written(self, pg_engine, tmp_path):
        from db.copy_to_cloud import copy_sqlite_to_postgres

        _upgrade(PG_URL)
        source = _local_db(tmp_path)
        before = source.read_bytes()
        copy_sqlite_to_postgres(source, PG_URL)
        assert source.read_bytes() == before
