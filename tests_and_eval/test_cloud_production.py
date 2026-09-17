"""Phase 9: the production entry points (cloud_api/settings.py, wsgi.py,
deploy_tasks.py) and the image allowlist.

Postgres tests need JHI_TEST_POSTGRES_URL.
"""

import importlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests_and_eval.test_cloud_db import PG_URL, needs_pg, pg_engine  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parent.parent

PROD_ENV = {
    "AWS_REGION": "us-east-1", "DB_HOST": "db.internal", "DB_PORT": "5432", "DB_NAME": "jhi",
    "JHI_APP_DB_USER": "jhi_api", "JHI_APP_DB_PASSWORD": "p@ss/word%", "JHI_ADMIN_DB_USER": "jhi_admin",
    "JHI_ADMIN_DB_PASSWORD": "secret", "COGNITO_USER_POOL_ID": "us-east-1_abc", "COGNITO_CLIENT_ID": "client",
    "RESUME_BUCKET": "resumes", "RESUME_KMS_KEY_ID": "key-id", "JHI_RESUME_KEY": "x" * 43 + "=",
    "CORS_ORIGINS": "https://app.example.com, https://example.com",
    "RESCORE_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/jhi-rescore",
}


@pytest.fixture
def prod_env(monkeypatch):
    from cryptography.fernet import Fernet

    for name, value in {**PROD_ENV, "JHI_RESUME_KEY": Fernet.generate_key().decode()}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DB_SSLMODE", raising=False)
    monkeypatch.delenv("JHI_DATABASE_URL", raising=False)


class TestSettings:
    def test_database_url_escapes_passwords_and_requires_tls(self, prod_env):
        from cloud_api.settings import database_url

        url = make_url(database_url("JHI_APP_DB_USER", "JHI_APP_DB_PASSWORD"))
        assert (url.username, url.password, url.host, url.database) == ("jhi_api", "p@ss/word%", "db.internal", "jhi")
        assert url.query["sslmode"] == "require"

    def test_a_missing_variable_stops_the_process(self, prod_env, monkeypatch):
        from cloud_api.settings import database_url

        monkeypatch.delenv("DB_HOST")
        with pytest.raises(RuntimeError, match="DB_HOST"):
            database_url("JHI_APP_DB_USER", "JHI_APP_DB_PASSWORD")

    def test_jwks_is_cached_and_refetched_for_a_new_key_id(self, monkeypatch):
        from cloud_api import settings

        fetches = []
        clock = [1000.0]
        monkeypatch.setattr(settings.time, "monotonic", lambda: clock[0])
        jwks = settings.CachedJwks("https://example/jwks.json",
                                   fetch=lambda: fetches.append(1) or {"keys": [{"kid": f"k{len(fetches)}"}]})
        jwks("k1"), jwks("k1")
        assert len(fetches) == 1
        jwks("new")                          # unknown key, but fetched under a minute ago
        assert len(fetches) == 1
        clock[0] += 61
        assert jwks("new")["keys"][0]["kid"] == "k2" and len(fetches) == 2
        clock[0] += 3601
        jwks("k2")
        assert len(fetches) == 3


    def test_resume_key_from_a_generated_secret_or_a_fernet_key(self):
        from cryptography.fernet import Fernet

        from cloud_api.settings import fernet_key_from_secret

        fernet = Fernet.generate_key()
        assert fernet_key_from_secret(fernet.decode()) == fernet
        derived = fernet_key_from_secret("a" * 64)
        assert derived == fernet_key_from_secret("a" * 64) != fernet_key_from_secret("b" * 64)
        Fernet(derived).encrypt(b"resume")

    def test_health_check_is_public_and_needs_no_database(self, prod_env):
        app = importlib.import_module("cloud_api.wsgi").build_app()
        assert app.test_client().get("/healthz").get_json() == {"ok": True}


class TestWsgi:
    def test_builds_the_production_app_with_cognito_and_s3(self, prod_env):
        from cloud_api.auth.verify import CognitoVerifier
        from resume.storage import S3ResumeStorage

        app = importlib.import_module("cloud_api.wsgi").build_app()
        verifier = app.config["VERIFIER"]
        assert isinstance(verifier, CognitoVerifier)
        assert verifier.issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abc"
        assert isinstance(app.config["STORAGE"], S3ResumeStorage) and app.config["STORAGE"].kms_key_id == "key-id"
        assert not [r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/dev-storage")]
        response = app.test_client().get("/api/v1/me", headers={"Authorization": "Bearer dev:a@example.com"})
        assert response.status_code == 401


@needs_pg
class TestDeployTask:
    def test_migrates_and_creates_logins_that_can_sign_in(self, pg_engine, prod_env, monkeypatch):  # noqa: F811
        from cloud_api import deploy_tasks

        url = make_url(PG_URL)
        for name, value in {"DB_HOST": url.host, "DB_PORT": str(url.port or 5432), "DB_NAME": url.database,
                            "DB_OWNER_USER": url.username, "DB_OWNER_PASSWORD": url.password or "unused",
                            "DB_SSLMODE": "disable", "JHI_APP_DB_USER": "jhi_api_deploytest",
                            "JHI_ADMIN_DB_USER": "jhi_admin_deploytest"}.items():
            monkeypatch.setenv(name, value)

        monkeypatch.setenv("JHI_OWNER_EMAIL", "Owner@Example.com")
        deploy_tasks.main(["migrate"])
        deploy_tasks.main(["migrate"])          # safe to run on every deploy

        with pg_engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            memberships = dict(conn.execute(text(
                "SELECT m.rolname, r.rolname FROM pg_auth_members a JOIN pg_roles r ON r.oid = a.roleid "
                "JOIN pg_roles m ON m.oid = a.member WHERE m.rolname LIKE '%deploytest'")).all())
            owner = conn.execute(text("SELECT role, idp_subject FROM users WHERE email = 'owner@example.com'")).all()
        assert memberships == {"jhi_api_deploytest": "jhi_app", "jhi_admin_deploytest": "jhi_admin_api"}
        assert owner == [("admin", None)]      # claimed by the first verified sign-in with that email
        engine = create_engine(url.set(username="jhi_api_deploytest", password="p@ss/word%"))
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM jobs")).scalar() == 0
        engine.dispose()

    def test_rejects_unknown_commands(self):
        from cloud_api import deploy_tasks

        with pytest.raises(SystemExit):
            deploy_tasks.main(["drop-everything"])


def test_the_image_allowlist_keeps_private_files_out():
    rules = [line.strip() for line in (REPO / ".dockerignore").read_text().splitlines()
             if line.strip() and not line.startswith("#")]
    assert rules[0] == "*", "the image context must start from nothing"
    included = [r[1:].rstrip("/") for r in rules if r.startswith("!")]
    assert set(included) == {"requirements-cloud.txt", "alembic.ini", "cloud_api", "db", "analysis", "judge", "resume"}
    copied = {line.split()[1].rstrip("/") for line in (REPO / "Dockerfile").read_text().splitlines()
              if line.startswith("COPY ")}
    assert copied <= set(included)
