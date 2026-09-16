"""Phase 2: per-user schema in the cloud database, and the owner as user #1.

Cloud only. The per-user tables are defined in db/cloud_models.py on their own
metadata, so the local SQLite schema and init_db() never see them, and the
shared tables (jobs, screening_results, ...) keep exactly the shape local code
reads and writes.

Postgres tests need JHI_TEST_POSTGRES_URL (see test_cloud_db.py).
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from db.job_writer import save_new_job
from db.models import Base, Job, ScreeningResult
from tests_and_eval.test_cloud_db import PG_URL, _upgrade, needs_pg, pg_engine  # noqa: F401  (fixture)
from tests_and_eval.test_jd_normalize import _detail


def test_cloud_tables_never_enter_the_local_schema():
    import db.cloud_models as cloud

    cloud_tables = set(cloud.CloudBase.metadata.tables)
    assert {"users", "user_profiles", "resumes", "job_seniority", "job_tracking",
            "application_events", "user_job_scores", "api_tokens"} <= cloud_tables
    assert not cloud_tables & set(Base.metadata.tables)


@pytest.fixture
def db(pg_engine):  # noqa: F811
    _upgrade(PG_URL)
    session = sessionmaker(bind=pg_engine)()
    yield session
    session.rollback()
    session.close()


def _job(db, linkedin_id: str = "1") -> Job:
    job = Job(job_id=linkedin_id, url=f"https://www.linkedin.com/jobs/view/{linkedin_id}",
              keyword_matched="llm remote", track="ml_ai")
    db.add(job)
    db.flush()
    return job


def _user(db, email: str = "a@example.com", **kw):
    from db.cloud_models import User

    user = User(email=email, **kw)
    db.add(user)
    db.flush()
    return user


@needs_pg
class TestConstraints:
    def test_one_tracking_row_per_user_and_job(self, db):
        from db.cloud_models import JobTracking

        user, job = _user(db), _job(db)
        db.add_all([JobTracking(user_id=user.id, job_id=job.id), JobTracking(user_id=user.id, job_id=job.id)])
        with pytest.raises(IntegrityError):
            db.flush()

    def test_one_score_row_per_user_and_job(self, db):
        from db.cloud_models import UserJobScore

        user, job = _user(db), _job(db)
        db.add_all([UserJobScore(user_id=user.id, job_id=job.id, profile_version=1),
                    UserJobScore(user_id=user.id, job_id=job.id, profile_version=1)])
        with pytest.raises(IntegrityError):
            db.flush()

    def test_profile_versions_are_unique_per_user(self, db):
        from db.cloud_models import UserProfile

        user = _user(db)
        db.add_all([UserProfile(user_id=user.id, version=1, seniority_target="entry"),
                    UserProfile(user_id=user.id, version=1, seniority_target="mid_senior")])
        with pytest.raises(IntegrityError):
            db.flush()

    def test_one_seniority_row_per_job(self, db):
        from db.cloud_models import JobSeniority

        job = _job(db)
        db.add_all([JobSeniority(job_id=job.id, level="mid_senior"), JobSeniority(job_id=job.id, level="senior")])
        with pytest.raises(IntegrityError):
            db.flush()

    def test_users_waiting_for_their_first_login_can_coexist(self, db):
        """idp_subject stays NULL until a login claims the row: the owner exists
        before the identity provider does."""
        _user(db, "a@example.com")
        _user(db, "b@example.com")

    def test_email_is_unique(self, db):
        _user(db, "a@example.com")
        with pytest.raises(IntegrityError):
            _user(db, "a@example.com")

    def test_unknown_role_is_rejected(self, db):
        with pytest.raises(IntegrityError):
            _user(db, role="owner")

    def test_unknown_seniority_level_is_rejected(self, db):
        from db.cloud_models import JobSeniority

        db.add(JobSeniority(job_id=_job(db).id, level="junior"))
        with pytest.raises(IntegrityError):
            db.flush()

    def test_unknown_application_stage_is_rejected(self, db):
        from db.cloud_models import ApplicationEvent

        user, job = _user(db), _job(db)
        db.add(ApplicationEvent(user_id=user.id, job_id=job.id, stage="ghosted", occurred_at=datetime(2026, 9, 1)))
        with pytest.raises(IntegrityError):
            db.flush()


APPLIED_AT = datetime(2026, 9, 1, 12, 0)


def _local_with_statuses(tmp_path):
    """A local SQLite DB where the owner applied to one job, passed on one,
    noted one, and left two untouched; four have seniority scores."""
    path = tmp_path / "local.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    words = ["alpha", "bravo", "charlie", "delta", "echo"]
    jobs = {i: save_new_job(session, "llm remote", "ml_ai", str(i), _detail(f"{words[i - 1]} python llm " * 30))
            for i in range(1, 6)}
    jobs[1].applied, jobs[1].applied_at, jobs[1].applied_resume_version = True, APPLIED_AT, "v2"
    jobs[2].not_interested, jobs[2].not_interested_note = True, "contract role"
    jobs[3].note = "recruiter call Friday"
    jobs[5].note = ""   # a cleared note: not a status (live job 4057), but must not linger on the shared row
    session.add_all([
        ScreeningResult(job_id=jobs[1].id, seniority_score=5, seniority_confidence="high",
                        seniority_years_required=1, seniority_inferred=False, seniority_evidence="new grad program"),
        ScreeningResult(job_id=jobs[2].id, seniority_score=3, seniority_confidence="low"),   # nothing inferable
        ScreeningResult(job_id=jobs[3].id, seniority_score=0, seniority_confidence="high"),  # waits for phase 4
        ScreeningResult(job_id=jobs[4].id, seniority_score=2, seniority_confidence="medium"),
    ])
    session.commit()
    session.close()
    engine.dispose()
    return path


@needs_pg
class TestSeedOwner:
    @pytest.fixture
    def seeded(self, pg_engine, tmp_path):  # noqa: F811
        from db.copy_to_cloud import copy_sqlite_to_postgres
        from db.seed_owner import seed_owner

        _upgrade(PG_URL)
        copy_sqlite_to_postgres(_local_with_statuses(tmp_path), PG_URL)
        report = seed_owner(PG_URL, email="owner@example.com", display_name="Owner")
        session = sessionmaker(bind=pg_engine)()
        yield report, session
        session.close()

    def test_owner_is_user_one_admin_with_an_entry_level_profile(self, seeded):
        from db.cloud_models import User, UserProfile

        _, db = seeded
        user = db.query(User).one()
        assert (user.id, user.role, user.email, user.idp_subject) == (1, "admin", "owner@example.com", None)
        profile = db.query(UserProfile).one()
        assert (profile.user_id, profile.version, profile.seniority_target) == (1, 1, "entry")
        from analysis.seniority_fit import proposed_scores

        assert profile.seniority_scores == proposed_scores("entry")   # reproduces today's rubric

    def test_statuses_move_to_job_tracking(self, seeded):
        from db.cloud_models import JobTracking

        report, db = seeded
        rows = {job_id: t for t, job_id in db.query(JobTracking, Job.job_id).join(Job, Job.id == JobTracking.job_id)}
        assert set(rows) == {"1", "2", "3"} and report.tracking_rows == 3
        assert (rows["1"].applied, rows["1"].applied_at, rows["1"].applied_resume_version) == (True, APPLIED_AT, "v2")
        assert (rows["2"].not_interested, rows["2"].not_interested_note) == (True, "contract role")
        assert rows["3"].note == "recruiter call Friday"
        assert all(t.user_id == 1 for t in rows.values())

    def test_the_shared_jobs_rows_no_longer_carry_anyones_status(self, seeded):
        _, db = seeded
        for job in db.query(Job):
            assert (job.applied, job.applied_at, job.applied_resume_version, job.not_interested,
                    job.not_interested_note, job.note) == (False, None, None, False, None, None)

    def test_an_applied_job_with_a_known_date_gets_an_applied_event(self, seeded):
        from db.cloud_models import ApplicationEvent

        _, db = seeded
        assert [(e.user_id, e.stage, e.occurred_at) for e in db.query(ApplicationEvent)] == [(1, "applied", APPLIED_AT)]

    def test_seniority_scores_backfill_job_levels(self, seeded):
        from db.cloud_models import JobSeniority

        _, db = seeded
        rows = {job_id: s for s, job_id in db.query(JobSeniority, Job.job_id).join(Job, Job.id == JobSeniority.job_id)}
        assert {k: (s.level, s.is_agency or s.is_contract) for k, s in rows.items()} == {
            "1": ("entry", False),
            "2": (None, False),         # score 3 at low confidence = nothing inferable
            "4": ("senior", False),
        }                              # score 0 mixes top-level roles with non-fit postings: re-classified in phase 4
        assert (rows["1"].years_required, rows["1"].inferred, rows["1"].evidence) == (1, False, "new grad program")

    def test_refuses_to_seed_twice(self, seeded):
        from db.seed_owner import seed_owner

        with pytest.raises(RuntimeError, match="already has users"):
            seed_owner(PG_URL, email="owner@example.com", display_name="Owner")
