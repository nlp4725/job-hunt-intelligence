"""The Lambda functions and their database roles (terraform/app/lambda.tf).

- rescore Lambda, login in jhi_scorer: scores queued jobs for every user,
  and cannot read tracking, application history, expertise data or tokens.
- admin Lambda, login in jhi_importer: status and the one-time SQLite import,
  which leaves out the owner's private tables; no per-user tables at all.

Postgres tests need JHI_TEST_POSTGRES_URL. The logins here use the test
server's trust authentication in place of IAM tokens.
"""

import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests_and_eval.test_cloud_captures import OWNER, _capture, classifier, client  # noqa: F401  (fixtures)
from tests_and_eval.test_cloud_db import PG_URL, _local_db, _upgrade, needs_pg, pg_engine  # noqa: F401  (fixture)
from tests_and_eval.test_cloud_isolation import admin_url, app_url  # noqa: F401  (fixtures)
from tests_and_eval.test_cloud_user_routes import A, _onboard


def _login(pg_engine, login: str, role: str) -> str:  # noqa: F811
    with pg_engine.begin() as conn:
        conn.execute(text(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{login}') "
                          f"THEN CREATE ROLE {login} LOGIN; END IF; END $$"))
        conn.execute(text(f"GRANT {role} TO {login}"))
    return make_url(PG_URL).set(username=login, password=None).render_as_string(hide_password=False)


@pytest.fixture
def scorer_url(pg_engine, app_url):  # noqa: F811
    return _login(pg_engine, "jhi_rescore_test", "jhi_scorer")


@pytest.fixture
def importer_url(pg_engine):  # noqa: F811
    _upgrade(PG_URL)
    return _login(pg_engine, "jhi_admin_task_test", "jhi_importer")


class FakeContext:
    def __init__(self, remaining_ms=300_000):
        self.remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self):
        return self.remaining_ms


def _sqs_event(*messages, bodies=None):
    import json

    bodies = bodies or [json.dumps(m) for m in messages]
    return {"Records": [{"messageId": f"m{i}", "body": body, "attributes": {"ApproximateReceiveCount": "1"}}
                        for i, body in enumerate(bodies)]}


@pytest.fixture
def quiet_client(client):  # noqa: F811
    """The capture fixture's client, with messages recorded but not scored inline,
    so the Lambda handler does the scoring."""
    from tests_and_eval.test_cloud_captures import RecordingPublisher

    client.published.clear()
    client.application.config["RESCORE_PUBLISHER"] = RecordingPublisher(client.published)
    return client


@needs_pg
class TestRescoreLambda:
    def test_sqs_messages_are_scored_as_the_narrow_role(self, quiet_client, scorer_url, pg_engine, monkeypatch):  # noqa: F811
        from cloud_api import lambda_handlers
        from db.cloud_models import UserJobScore

        client = quiet_client
        _onboard(client, A, "SKILLS\nPython, SQL, LLM\n", "entry")
        job_ids = [_capture(client, str(n), company=f"Co{n}").get_json()["job"]["id"] for n in (200, 201)]
        monkeypatch.setenv("JHI_DATABASE_URL", scorer_url)

        result = lambda_handlers.rescore(_sqs_event(*client.published), FakeContext())

        assert result == {"batchItemFailures": []}
        assert [m["type"] for m in client.published] == ["user", "user", "job", "job"]   # level, skills, two captures
        with Session(pg_engine) as db:
            assert {s.job_id for s in db.query(UserJobScore).filter(UserJobScore.job_id.in_(job_ids))} == set(job_ids)

    def test_only_failed_messages_are_retried(self, scorer_url, pg_engine, monkeypatch):  # noqa: F811
        from cloud_api import lambda_handlers

        _upgrade(PG_URL)
        monkeypatch.setenv("JHI_DATABASE_URL", scorer_url)
        event = _sqs_event(bodies=['{"type": "job", "job_id": 999999}', "not json", '{"type": "delete_everything"}'])
        result = lambda_handlers.rescore(event, FakeContext())
        assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}, {"itemIdentifier": "m2"}]}   # a missing job is not an error

    def test_reconcile_scores_what_lost_messages_missed(self, quiet_client, scorer_url, pg_engine, monkeypatch):  # noqa: F811
        from cloud_api import lambda_handlers
        from db.cloud_models import UserJobScore

        client = quiet_client
        _onboard(client, A, "SKILLS\nPython, SQL, LLM\n", "entry")   # messages recorded, never delivered
        _capture(client, "300")
        monkeypatch.setenv("JHI_DATABASE_URL", scorer_url)

        first = lambda_handlers.rescore({"type": "reconcile"}, FakeContext())
        assert first["stale_users"] == 1                     # never scored: full board
        with Session(pg_engine) as db:
            assert db.query(UserJobScore).count() >= 1

        _capture(client, "301", company="Other")               # one more lost job message
        assert lambda_handlers.rescore({"type": "reconcile"}, FakeContext()) == {"stale_users": 0, "missing_scores": 1}
        assert lambda_handlers.rescore({"type": "reconcile"}, FakeContext()) == {"stale_users": 0, "missing_scores": 0}

    def test_a_scoring_logic_change_rescores_everyone(self, quiet_client, scorer_url, pg_engine, monkeypatch):  # noqa: F811
        import analysis.rescoring
        from cloud_api import lambda_handlers

        client = quiet_client
        _onboard(client, A, "SKILLS\nPython\n", "entry")
        _capture(client, "400")
        monkeypatch.setenv("JHI_DATABASE_URL", scorer_url)
        lambda_handlers.rescore({"type": "reconcile"}, FakeContext())
        monkeypatch.setattr(analysis.rescoring, "SCORING_VERSION", "skill-seniority-2")
        monkeypatch.setattr("analysis.user_scoring.SCORING_VERSION", "skill-seniority-2")
        assert lambda_handlers.rescore({"type": "reconcile"}, FakeContext())["stale_users"] == 1

    def test_the_scorer_role_cannot_read_private_activity(self, client, scorer_url):  # noqa: F811
        engine = create_engine(scorer_url)
        for table in ("job_tracking", "application_events", "expertise_profiles", "user_job_expertise", "api_tokens"):
            with engine.connect() as conn, pytest.raises(DBAPIError):
                conn.execute(text(f"SELECT count(*) FROM {table}"))
        with engine.connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text("UPDATE jobs SET title = 'x'"))
        engine.dispose()


@needs_pg
class TestAdminLambda:
    def test_status(self, importer_url, monkeypatch):
        from cloud_api import lambda_handlers

        monkeypatch.setenv("JHI_DATABASE_URL", importer_url)
        assert lambda_handlers.admin({"command": "status"}, None) == {
            "jobs": 0, "jobs_last_24h": 0, "jobs_with_level": 0}

    def test_only_listed_commands_run(self, importer_url, monkeypatch):
        from cloud_api import lambda_handlers

        monkeypatch.setenv("JHI_DATABASE_URL", importer_url)
        for event in ({"command": "DROP TABLE jobs"}, {}, None):
            with pytest.raises(ValueError):
                lambda_handlers.admin(event, None)

    def test_import_copies_shared_tables_and_leaves_out_private_ones(self, importer_url, pg_engine, tmp_path, monkeypatch):  # noqa: F811
        from cloud_api import lambda_handlers

        source = _local_db(tmp_path)
        with sqlite3.connect(source) as conn:
            conn.execute("INSERT INTO resume (track, content, uploaded_at, updated_at) VALUES ('ml_ai', 'private resume', '2026-09-01', '2026-09-01')")

        class FakeS3:
            deleted = []

            def download_file(self, bucket, key, path):
                assert (bucket, key) == ("imports-bucket", "imports/job_hunt.db")
                path_bytes = source.read_bytes()
                open(path, "wb").write(path_bytes)

            def delete_object(self, Bucket, Key):
                self.deleted.append(Key)

        fake = FakeS3()
        monkeypatch.setattr("boto3.client", lambda *a, **k: fake)
        monkeypatch.setenv("JHI_DATABASE_URL", importer_url)
        monkeypatch.setenv("IMPORT_BUCKET", "imports-bucket")
        monkeypatch.setenv("AWS_REGION", "us-east-1")

        result = lambda_handlers.admin({"command": "import_sqlite", "s3_key": "imports/job_hunt.db"}, None)

        assert result["copied"]["jobs"] == 2 and "resume" not in result["copied"]
        assert result["left_out"] == ["career_goals", "chat_messages", "resume"]
        assert fake.deleted == ["imports/job_hunt.db"]
        with pg_engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM resume")).scalar() == 0
            assert conn.execute(text("SELECT count(*) FROM jobs")).scalar() == 2

    def test_import_levels_carries_classified_levels_by_linkedin_id(self, importer_url, pg_engine, tmp_path, monkeypatch):  # noqa: F811
        """Levels live in a cloud-only table, so they travel separately from the
        SQLite seed, keyed by LinkedIn job id (internal ids differ per database)."""
        import gzip
        import json

        from cloud_api import lambda_handlers
        from db.level_transfer import export_levels

        from db.models import Job

        with Session(pg_engine) as db, db.begin():   # two jobs here; the export also holds a level for a job we don't have
            db.add_all([Job(job_id=linkedin_id, url=f"u{linkedin_id}", keyword_matched="llm", track="ml_ai")
                        for linkedin_id in ("900", "901")])
        export = tmp_path / "levels.jsonl.gz"
        with gzip.open(export, "wt") as out:
            for linkedin_id, level in (("900", "senior"), ("999", "entry")):
                out.write(json.dumps({"linkedin_id": linkedin_id, "level": level, "is_contract": False,
                                      "years_required": 6 if level == "senior" else None, "inferred": True,
                                      "confidence": "high", "evidence": "e", "note": None,
                                      "prompt_version": "seniority_level_v4", "classified_at": "2026-09-01T10:00:00"}) + "\n")

        class FakeS3:
            deleted = []

            def download_file(self, bucket, key, path):
                open(path, "wb").write(export.read_bytes())

            def delete_object(self, Bucket, Key):
                self.deleted.append(Key)

        fake = FakeS3()
        monkeypatch.setattr("boto3.client", lambda *a, **k: fake)
        monkeypatch.setenv("JHI_DATABASE_URL", importer_url)
        monkeypatch.setenv("IMPORT_BUCKET", "imports-bucket")
        monkeypatch.setenv("AWS_REGION", "us-east-1")

        result = lambda_handlers.admin({"command": "import_levels", "s3_key": "imports/levels.jsonl.gz"}, None)

        assert result == {"read": 2, "written": 1, "unknown": 1}
        with pg_engine.connect() as conn:
            rows = conn.execute(text("SELECT j.job_id, s.level, s.years_required, s.prompt_version FROM job_seniority s "
                                     "JOIN jobs j ON j.id = s.job_id")).all()
        assert rows == [("900", "senior", 6, "seniority_level_v4")]
        assert fake.deleted == ["imports/levels.jsonl.gz"]

        # exporting from this database gives back what an import would take
        out = tmp_path / "roundtrip.jsonl.gz"
        assert export_levels(importer_url, out) == 1
        assert json.loads(gzip.open(out, "rt").readline())["linkedin_id"] == "900"

    def test_import_keys_must_be_under_imports(self, importer_url, monkeypatch):
        from cloud_api import lambda_handlers

        monkeypatch.setenv("JHI_DATABASE_URL", importer_url)
        with pytest.raises(ValueError):
            lambda_handlers.admin({"command": "import_sqlite", "s3_key": "../resumes/users/1/cv.pdf"}, None)

    def test_the_importer_role_cannot_read_per_user_tables(self, importer_url):
        engine = create_engine(importer_url)
        for table in ("resumes", "user_profiles", "user_job_scores", "job_tracking", "api_tokens"):
            with engine.connect() as conn, pytest.raises(DBAPIError):
                conn.execute(text(f"SELECT count(*) FROM {table}"))
        engine.dispose()


@needs_pg
def test_deploy_task_creates_the_lambda_logins(pg_engine, monkeypatch):  # noqa: F811
    from cloud_api import deploy_tasks

    url = make_url(PG_URL)
    for name, value in {"DB_HOST": url.host, "DB_PORT": str(url.port or 5432), "DB_NAME": url.database,
                        "DB_OWNER_USER": url.username, "DB_OWNER_PASSWORD": url.password or "unused",
                        "DB_SSLMODE": "disable", "JHI_APP_DB_USER": "jhi_api_deploytest", "JHI_APP_DB_PASSWORD": "x",
                        "JHI_ADMIN_DB_USER": "jhi_admin_deploytest", "JHI_ADMIN_DB_PASSWORD": "y"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("JHI_DATABASE_URL", raising=False)
    deploy_tasks.main(["migrate"])
    with pg_engine.connect() as conn:
        members = dict(conn.execute(text(
            "SELECT m.rolname, r.rolname FROM pg_auth_members a JOIN pg_roles r ON r.oid = a.roleid "
            "JOIN pg_roles m ON m.oid = a.member WHERE m.rolname IN ('jhi_rescore', 'jhi_admin_task')")).all())
    assert members == {"jhi_rescore": "jhi_scorer", "jhi_admin_task": "jhi_importer"}
