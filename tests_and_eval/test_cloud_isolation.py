"""Phase 5, slice 2: separate user access (productization plan §5.2).

Layer 2, the access layer: cloud_api routes reach per-user tables only through
cloud_api/user_data.py.
Layer 3, Postgres row-level security: the API connects as the jhi_app role,
which does not own the tables, so RLS applies. Each request sets app.user_id
for its own transaction; without it no per-user row is visible, and a user can
never write another user's rows. The owner role (migrations, batch jobs) is
unaffected.

These tests connect as a restricted login in the jhi_app role: a superuser
would bypass RLS and prove nothing. Postgres tests need JHI_TEST_POSTGRES_URL.
"""

import ast
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests_and_eval.test_cloud_db import PG_URL, _upgrade, needs_pg, pg_engine  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parent.parent
APP_LOGIN = "jhi_api_test"


def _per_user_models():
    from db.cloud_models import ApplicationEvent, JobTracking, UserJobScore, UserProfile, UserResume

    return [UserResume, UserProfile, UserJobScore, JobTracking, ApplicationEvent]


@pytest.fixture
def app_url(pg_engine):  # noqa: F811
    """A login that is a member of jhi_app and nothing else."""
    _upgrade(PG_URL)
    with pg_engine.begin() as conn:
        conn.execute(text(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_LOGIN}') "
                          f"THEN CREATE ROLE {APP_LOGIN} LOGIN; END IF; END $$"))
        conn.execute(text(f"GRANT jhi_app TO {APP_LOGIN}"))
    return make_url(PG_URL).set(username=APP_LOGIN, password=None).render_as_string(hide_password=False)


@pytest.fixture
def app_engine(app_url):
    engine = create_engine(app_url, connect_args={"options": "-c timezone=UTC"})
    yield engine
    engine.dispose()


@pytest.fixture
def seeded(pg_engine, app_url):  # noqa: F811
    """Two users (a@, b@ as dev logins) with one row each in every per-user table, written as the owner."""
    from db.cloud_models import ApplicationEvent, JobTracking, User, UserJobScore, UserProfile, UserResume
    from db.models import Job

    with Session(pg_engine) as db, db.begin():
        job = Job(job_id="1", url="https://www.linkedin.com/jobs/view/1", keyword_matched="llm remote", track="ml_ai")
        db.add(job)
        ids = {}
        for name in ("a", "b"):
            user = User(email=f"{name}@example.com", idp_subject=f"dev|{name}@example.com")
            db.add(user)
            db.flush()
            db.add_all([
                UserResume(user_id=user.id, version=1, skills_confirmed=["Python"]),
                UserProfile(user_id=user.id, version=1, seniority_target="entry"),
                UserJobScore(user_id=user.id, job_id=job.id, profile_version=1, skill_score=3),
                JobTracking(user_id=user.id, job_id=job.id, note=f"{name}'s private note"),
                ApplicationEvent(user_id=user.id, job_id=job.id, stage="applied", occurred_at=datetime(2026, 9, 1)),
            ])
            ids[name] = user.id
        ids["job"] = job.id
    return ids


@needs_pg
class TestRowLevelSecurity:
    def test_without_a_user_setting_no_per_user_row_is_visible(self, app_engine, seeded):
        with Session(app_engine) as db:
            for model in _per_user_models():
                assert db.query(model).count() == 0, model.__tablename__

    def test_with_a_user_setting_only_that_users_rows_are_visible(self, app_engine, seeded):
        from cloud_api.user_data import set_request_user

        with Session(app_engine) as db, db.begin():
            set_request_user(db, seeded["a"])
            for model in _per_user_models():
                rows = db.query(model).all()
                assert len(rows) == 1 and rows[0].user_id == seeded["a"], model.__tablename__

    def test_raw_sql_is_filtered_too(self, app_engine, seeded):
        from cloud_api.user_data import set_request_user

        with Session(app_engine) as db, db.begin():
            set_request_user(db, seeded["a"])
            notes = [row[0] for row in db.execute(text("SELECT note FROM job_tracking"))]
        assert notes == ["a's private note"]

    def test_the_setting_ends_with_the_transaction(self, app_engine, seeded):
        from cloud_api.user_data import set_request_user
        from db.cloud_models import JobTracking

        with Session(app_engine) as db:
            with db.begin():
                set_request_user(db, seeded["a"])
                assert db.query(JobTracking).count() == 1
            with db.begin():
                assert db.query(JobTracking).count() == 0

    def test_a_user_cannot_insert_rows_for_another_user(self, app_engine, seeded):
        from cloud_api.user_data import set_request_user
        from db.cloud_models import JobTracking

        with Session(app_engine) as db:
            db.begin()
            set_request_user(db, seeded["a"])
            db.execute(text("DELETE FROM job_tracking"))       # only a's own row can go
            db.add(JobTracking(user_id=seeded["b"], job_id=seeded["job"], note="written by a"))
            with pytest.raises(DBAPIError):
                db.flush()
            db.rollback()

    def test_a_user_cannot_change_or_delete_another_users_rows(self, app_engine, seeded, pg_engine):  # noqa: F811
        from cloud_api.user_data import set_request_user
        from db.cloud_models import JobTracking

        with Session(app_engine) as db, db.begin():
            set_request_user(db, seeded["a"])
            changed = db.query(JobTracking).filter(JobTracking.user_id == seeded["b"]).update({"note": "hacked"})
            deleted = db.query(JobTracking).filter(JobTracking.user_id == seeded["b"]).delete()
        assert (changed, deleted) == (0, 0)
        with Session(pg_engine) as owner:
            assert owner.query(JobTracking).filter_by(user_id=seeded["b"]).one().note == "b's private note"

    def test_the_owner_role_for_migrations_and_batch_jobs_sees_every_row(self, pg_engine, seeded):  # noqa: F811
        with Session(pg_engine) as db:
            for model in _per_user_models():
                assert db.query(model).count() == 2, model.__tablename__


@needs_pg
class TestApiUnderRowLevelSecurity:
    @pytest.fixture
    def client(self, app_url, seeded):
        from cloud_api.app import create_app
        from cloud_api.auth.verify import FakeVerifier

        app = create_app(app_url, verifier=FakeVerifier(), auth_mode="dev", host="127.0.0.1")
        app.config["TESTING"] = True
        return app.test_client()

    def test_each_user_sees_only_their_own_onboarding_state(self, client):
        a = client.get("/api/v1/me", headers={"Authorization": "Bearer dev:a@example.com"}).get_json()
        c = client.get("/api/v1/me", headers={"Authorization": "Bearer dev:c@example.com"}).get_json()
        assert a["onboarding"]["resume"] and a["onboarding"]["level"]
        assert not c["onboarding"]["resume"] and not c["onboarding"]["level"]   # a's and b's rows are invisible to c

    def test_admin_api_tokens_work_for_the_app_role(self, client, pg_engine):  # noqa: F811
        from db.cloud_models import User

        with Session(pg_engine) as db, db.begin():
            db.add(User(email="owner@example.com", role="admin"))
        owner = {"Authorization": "Bearer dev:owner@example.com"}
        created = client.post("/api/v1/admin/tokens", json={"label": "extension"}, headers=owner).get_json()
        with_token = {"Authorization": f"Bearer {created['token']}"}
        assert client.post("/api/v1/admin/tokens", json={"label": "second"}, headers=with_token).status_code == 201


def test_cloud_api_reaches_per_user_tables_only_through_the_access_layer():
    """A new route that queries a per-user model directly fails here, before RLS
    has to catch it."""
    per_user = {"UserResume", "UserProfile", "UserJobScore", "JobTracking", "ApplicationEvent"}
    offenders = []
    for path in sorted((REPO / "cloud_api").rglob("*.py")):
        if path.name == "user_data.py":
            continue
        tree = ast.parse(path.read_text())
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        used |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
        offenders += [f"{path.relative_to(REPO)}: {name}" for name in sorted(used & per_user)]
    assert offenders == []
