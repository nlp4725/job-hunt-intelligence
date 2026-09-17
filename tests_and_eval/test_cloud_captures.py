"""Phase 6A: collection into the cloud (productization plan §2, §6).

The extension sends each capture to POST /api/v1/admin/captures with a
collector token. The admin route saves the job with the same code as the local
app, filters agencies and classifies the job's seniority level once. It cannot
write any user's scores (the admin role has no access to per-user tables), so
after its transaction commits it publishes a "score this job" message; the
rescore Lambda scores the job for every user with a confirmed resume. Here the
publisher records messages and scores them inline.

Postgres tests need JHI_TEST_POSTGRES_URL. Classification is a fake: no LLM.
"""

import pytest
from sqlalchemy.orm import Session

from judge.seniority_level import JobSeniorityLevel
from tests_and_eval.test_cloud_db import PG_URL, needs_pg, pg_engine  # noqa: F401  (fixture)
from tests_and_eval.test_cloud_isolation import admin_url, app_url  # noqa: F401  (fixtures)
from tests_and_eval.test_cloud_user_routes import A, _onboard

OWNER = {"Authorization": "Bearer dev:owner@example.com"}


def _detail(linkedin_id, company="Acme", title="ML Engineer", text="Build LLM systems with Python and SQL. " * 10):
    return {"url": f"https://www.linkedin.com/jobs/view/{linkedin_id}", "title": title, "company": company,
            "industry": "Software Development", "company_size": "51-200 employees", "location": "United States",
            "workplace_type": "Remote", "raw_text": text, "salary_text": None, "posted_date": "1 day ago",
            "applicant_stats": None}


class FakeClassifier:
    def __init__(self, level="entry", fail=False):
        self.level, self.fail, self.calls = level, fail, 0

    def __call__(self, posting: str) -> JobSeniorityLevel:
        self.calls += 1
        if self.fail:
            raise RuntimeError("model unavailable")
        return JobSeniorityLevel(evidence="e", years_required=None, inferred=True, confidence="high",
                                 note=None, is_contract=False, level=self.level)


class RecordingPublisher:
    def __init__(self, published, inner=None, fail=False):
        self.published, self.inner, self.fail = published, inner, fail

    def publish(self, messages):
        if self.fail:
            raise RuntimeError("SQS unavailable")
        self.published.extend(messages)
        if self.inner:
            self.inner.publish(messages)


@pytest.fixture
def classifier():
    return FakeClassifier()


@pytest.fixture
def client(app_url, admin_url, pg_engine, classifier, tmp_path):  # noqa: F811
    published = []
    from cryptography.fernet import Fernet

    from cloud_api.app import create_app
    from cloud_api.rescore import InlinePublisher
    from cloud_api.auth.verify import FakeVerifier
    from db.cloud_models import User
    from resume.storage import DevSignedStorage
    from resume.store import ResumeCipher

    with Session(pg_engine) as db, db.begin():
        db.add(User(email="owner@example.com", role="admin"))
    app = create_app(app_url, admin_database_url=admin_url, verifier=FakeVerifier(), auth_mode="dev", host="127.0.0.1",
                     storage=DevSignedStorage(root=tmp_path / "files", secret=b"s", base_url="http://localhost"),
                     cipher=ResumeCipher(Fernet.generate_key()), classify=classifier,
                     rescore_publisher=RecordingPublisher(published, InlinePublisher(PG_URL)))
    app.config["TESTING"] = True
    client = app.test_client()
    client.published = published
    token = client.post("/api/v1/admin/tokens", json={"label": "extension"}, headers=OWNER).get_json()["token"]
    client.extension = {"Authorization": f"Bearer {token}"}
    return client


def _capture(client, linkedin_id, **detail):
    """The same flat body the extension already sends to the local /api/extension/jobs."""
    return client.post("/api/v1/admin/captures", headers=client.extension,
                       json={"job_id": linkedin_id, **_detail(linkedin_id, **detail),
                             "extraction_meta": {"strategies": {"title": "builtin"}, "failed_fields": []}})


def _job_messages(client):
    return [m["job_id"] for m in client.published if m["type"] == "job"]


@needs_pg
class TestCaptures:
    def test_a_new_capture_is_saved_classified_once_and_queued(self, client, classifier, pg_engine):  # noqa: F811
        from db.cloud_models import JobSeniority
        from db.models import Job, JobSkill

        response = _capture(client, "100")
        body = response.get_json()
        assert response.status_code == 200 and body["status"] == "scored"
        assert body["seniority"] == {"level": "entry", "is_contract": False}
        assert _capture(client, "100").get_json()["status"] == "scored"   # a revisit
        assert classifier.calls == 1

        with Session(pg_engine) as db:
            job = db.query(Job).filter_by(job_id="100").one()
            assert {"Python", "SQL", "LLM"} <= {s for (s,) in db.query(JobSkill.skill_name).filter_by(job_id=job.id)}
            assert db.query(JobSeniority).filter_by(job_id=job.id).one().prompt_version
        assert _job_messages(client) == [body["job"]["id"], body["job"]["id"]]   # a revisit rescores too: harmless upsert

    def test_an_agency_posting_is_blocked_before_classification(self, client, classifier, pg_engine):  # noqa: F811
        body = _capture(client, "101", company="MeeBoss").get_json()
        assert (body["status"], body["reason"]) == ("blocked", "agency")
        assert classifier.calls == 0 and _job_messages(client) == []

    def test_a_repost_is_marked_duplicate_and_not_classified_again(self, client, classifier, pg_engine):  # noqa: F811
        first = _capture(client, "102").get_json()
        repost = _capture(client, "103").get_json()
        assert (repost["status"], repost["duplicate_of"]) == ("duplicate", first["job"]["id"])
        assert classifier.calls == 1 and _job_messages(client) == [first["job"]["id"]]

    def test_a_failed_classification_still_saves_and_queues_the_job(self, client, classifier, pg_engine):  # noqa: F811
        classifier.fail = True
        body = _capture(client, "104").get_json()
        assert body["status"] == "saved" and body["seniority"] is None
        assert _job_messages(client) == [body["job"]["id"]]

    def test_a_capture_without_title_or_text_is_400(self, client):
        response = client.post("/api/v1/admin/captures", headers=client.extension,
                               json={"job_id": "105", **_detail("105"), "raw_text": ""})
        assert response.status_code == 400

    def test_cached_lookup(self, client):
        assert client.get("/api/v1/admin/captures/106", headers=client.extension).status_code == 404
        _capture(client, "106")
        cached = client.get("/api/v1/admin/captures/106", headers=client.extension).get_json()
        assert cached["seniority"] == {"level": "entry", "is_contract": False}


@needs_pg
class TestCollectionSupport:
    def test_agency_list_for_the_skill(self, client):
        agencies = client.get("/api/v1/admin/agencies", headers=client.extension).get_json()
        assert "meeboss" in agencies["name_substrings"]

    def test_collection_pages_are_recorded(self, client, pg_engine):  # noqa: F811
        from db.models import CollectionPage

        page = {"session_id": "s1", "keyword": "llm remote", "endpoint": "literal", "page": 1,
                "rendered": 25, "skipped": 20, "clicked": 5, "pages_planned": 10}
        assert client.post("/api/v1/admin/collection-pages", json=page, headers=client.extension).status_code == 201
        assert client.post("/api/v1/admin/collection-pages", json={"page": 1}, headers=client.extension).status_code == 400
        with Session(pg_engine) as db:
            assert db.query(CollectionPage).filter_by(session_id="s1").one().clicked == 5

    def test_expire_a_job(self, client, pg_engine):  # noqa: F811
        from db.models import Job

        job_id = _capture(client, "107").get_json()["job"]["id"]
        assert client.patch(f"/api/v1/admin/jobs/{job_id}", json={"expired": True}, headers=client.extension).status_code == 200
        with Session(pg_engine) as db:
            assert db.get(Job, job_id).expired is True


@needs_pg
class TestRescoreMessages:
    def test_a_capture_scores_the_job_for_every_onboarded_user(self, client, pg_engine):  # noqa: F811
        from db.cloud_models import UserJobScore

        _onboard(client, A, "SKILLS\nPython, SQL, LLM\n", "entry")      # user a: confirmed resume + level
        client.get("/api/v1/me", headers={"Authorization": "Bearer dev:b@example.com"})   # user b: signed in only
        job_id = _capture(client, "108").get_json()["job"]["id"]

        with Session(pg_engine) as db:
            score = db.query(UserJobScore).filter_by(job_id=job_id).one()
            assert (score.seniority_fit, score.skill_score, score.scoring_version) == (5, 5, "skill-seniority-1")

    def test_a_request_that_fails_after_queuing_publishes_nothing(self, client, monkeypatch):  # noqa: F811
        """The message is queued, then the response can't be built: the request
        rolls back and nothing is published."""
        from cloud_api import admin_data

        original = admin_data.capture
        monkeypatch.setattr(admin_data, "capture", lambda db, body, classify: {**original(db, body, classify), "bad": object()})
        response = _capture(client, "109")
        assert response.status_code == 500 and response.get_json()["request_id"]
        assert client.published == []

    def test_a_failed_publish_does_not_fail_the_capture(self, client, pg_engine, capsys):  # noqa: F811
        client.application.config["RESCORE_PUBLISHER"] = RecordingPublisher([], fail=True)
        response = _capture(client, "110")
        assert response.status_code == 200
        logs = capsys.readouterr().out
        assert '"RescorePublishFailed"' in logs and '"event":"rescore_publish_failed"' in logs
