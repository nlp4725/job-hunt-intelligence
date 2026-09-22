"""The delta copy (db/backfill_to_cloud.py): catching the cloud up on local rows.

copy_to_cloud seeds an empty database; this is what runs every time after. The
cases that matter are the ones that would quietly corrupt the board: inserting a
row twice, attaching rows to an id that means a different job up there, and
leaving a Postgres sequence parked on an id that already exists.

Needs JHI_TEST_POSTGRES_URL, like the other Postgres tests.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from db.backfill_to_cloud import IdsDiverged, backfill_sqlite_to_postgres
from db.copy_to_cloud import PRIVATE_TABLES, copy_sqlite_to_postgres
from db.models import Base, Job, ScreeningResult
from db.job_writer import save_new_job
from tests_and_eval.test_cloud_db import JD, PG_URL, _detail, _local_db, _upgrade, needs_pg, pg_engine  # noqa: F401


def _add_job(path, linkedin_id: str, *, score: int | None = None) -> None:
    """One more capture in the local database, exactly as the extension path writes it."""
    engine = create_engine(f"sqlite:///{path}")
    session = sessionmaker(bind=engine)()
    job = save_new_job(session, "llm remote", "ml_ai", linkedin_id, _detail(JD))
    if score is not None:
        session.add(ScreeningResult(job_id=job.id, skill_score=3, total_score=score))
    session.commit()
    session.close()
    engine.dispose()


@needs_pg
class TestBackfill:
    def test_inserts_only_what_is_missing(self, pg_engine, tmp_path):  # noqa: F811
        """The seed runs, collection continues locally, and the delta carries just the new rows."""
        _upgrade(PG_URL)
        source = _local_db(tmp_path)
        copy_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)
        _add_job(source, "200", score=12)
        _add_job(source, "201")

        result = backfill_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)

        assert result.inserted["jobs"] == 2
        assert result.already_there["jobs"] == 2          # the two the seed brought
        assert result.inserted["screening_results"] == 1
        with pg_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM jobs")).scalar() == 4
            assert set(conn.execute(text("SELECT job_id FROM jobs")).scalars()) == {"100", "101", "200", "201"}

    def test_running_it_twice_changes_nothing(self, pg_engine, tmp_path):  # noqa: F811
        _upgrade(PG_URL)
        source = _local_db(tmp_path)
        copy_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)
        _add_job(source, "200")

        first = backfill_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)
        second = backfill_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)

        assert first.inserted["jobs"] == 1 and second.inserted["jobs"] == 0
        with pg_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM jobs")).scalar() == 3

    def test_the_cloud_can_still_insert_afterwards(self, pg_engine, tmp_path):  # noqa: F811
        """Backfilled rows keep their local ids, so every sequence has to clear them."""
        _upgrade(PG_URL)
        source = _local_db(tmp_path)
        copy_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)
        _add_job(source, "200")
        backfill_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)

        session = sessionmaker(bind=pg_engine)()     # what a capture posted straight to the API does
        try:
            new_id = save_new_job(session, "llm remote", "ml_ai", "300", _detail(JD)).id
            session.commit()
        finally:
            session.close()
        assert new_id == 4                   # the next free id, not a collision with the backfilled row

    def test_refuses_when_an_id_means_a_different_job_up_there(self, pg_engine, tmp_path):  # noqa: F811
        """The guard that stops this from stapling a job's rows onto another job."""
        _upgrade(PG_URL)
        source = _local_db(tmp_path)
        copy_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)
        with pg_engine.begin() as conn:      # the cloud's row 1 is now some other posting
            conn.execute(text("UPDATE jobs SET job_id = '999' WHERE id = 1"))
        _add_job(source, "200")

        with pytest.raises(IdsDiverged) as err:
            backfill_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)

        assert "999" in str(err.value)
        with pg_engine.connect() as conn:    # nothing was written
            assert conn.execute(text("SELECT count(*) FROM jobs")).scalar() == 2

    def test_private_tables_stay_local(self, pg_engine, tmp_path):  # noqa: F811
        _upgrade(PG_URL)
        source = _local_db(tmp_path)
        copy_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)
        with sqlite3.connect(source) as conn:
            conn.execute("INSERT INTO resume (track, content, uploaded_at, updated_at) "
                         "VALUES ('ml_ai', 'private resume', '2026-09-01', '2026-09-01')")
        _add_job(source, "200")

        result = backfill_sqlite_to_postgres(source, PG_URL, exclude=PRIVATE_TABLES)

        assert "resume" not in result.inserted
        with pg_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM resume")).scalar() == 0


@needs_pg
class TestReadiness:
    """The counts behind "0 stale, 0 missing": an empty scorer queue means
    either everyone is scored or nobody qualifies, and those look the same."""

    def test_counts_each_step_of_the_ready_chain(self, pg_engine, tmp_path):  # noqa: F811
        from analysis.rescoring import readiness
        from db.cloud_models import User, UserProfile, UserResume

        _upgrade(PG_URL)
        session = sessionmaker(bind=pg_engine)()
        try:
            signed_up = User(email="nobody@example.com", role="user")
            half_way = User(email="halfway@example.com", role="user")
            session.add_all([signed_up, half_way])
            session.flush()
            resume = UserResume(user_id=half_way.id, version=1, original_filename="cv.pdf")   # skills never confirmed
            session.add(resume)
            session.flush()
            session.add(UserProfile(user_id=half_way.id, version=1, resume_id=resume.id, seniority_targets=["entry"]))
            session.commit()

            report = readiness(session)
        finally:
            session.close()

        assert report["users"] == 2                        # both signed up
        assert report["with_a_profile"] == 1               # only one got that far
        assert report["profile_points_at_a_resume"] == 1
        assert report["resume_skills_confirmed"] == 0      # the step that was never done
        assert report["ready_to_score"] == 0               # so the scorer sees nobody
        assert report["users_with_any_score"] == 0
