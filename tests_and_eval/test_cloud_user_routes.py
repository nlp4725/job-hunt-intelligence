"""Phase 5, slice 3B: shared-table protection and the user routes.

Two database roles (productization plan §5.2, layer 3):
- jhi_app (user requests): reads the shared board tables, never writes them;
  reads and writes only its own per-user rows (RLS); may create plain users
  and update only its own users row, never the role column.
- jhi_admin_api (admin routes): reads and writes shared tables and api_tokens,
  and cannot touch any per-user table.

Routes: profile (level, score table), the job board with the caller's own
scores and tracking, tracking, applications and account deletion.
Postgres tests need JHI_TEST_POSTGRES_URL.
"""

import io
from urllib.parse import urlparse

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests_and_eval.test_cloud_db import PG_URL, needs_pg, pg_engine  # noqa: F401  (fixture)
from tests_and_eval.test_cloud_isolation import admin_url, app_url  # noqa: F401  (fixtures)

A = {"Authorization": "Bearer dev:a@example.com"}
B = {"Authorization": "Bearer dev:b@example.com"}
OWNER = {"Authorization": "Bearer dev:owner@example.com"}


@pytest.fixture
def board(pg_engine, app_url):  # noqa: F811
    """Shared data written as the owner: three board jobs, a duplicate and an agency posting."""
    from db.cloud_models import JobSeniority, User
    from db.models import Company, Job, JobSkill

    with Session(pg_engine) as db, db.begin():
        acme, beta, agency = Company(name="Acme"), Company(name="Beta"), Company(name="MeeBoss")
        db.add_all([acme, beta, agency])
        db.flush()
        jobs = {}
        for linkedin_id, title, company, skills, level in (
            ("1", "ML Engineer", acme, ["Python", "SQL"], "entry"),
            ("2", "Staff Engineer", acme, ["Python", "Kubernetes"], "staff_principal"),
            ("3", "Data Analyst", beta, ["SQL"], "mid_senior"),
        ):
            job = Job(job_id=linkedin_id, url=f"https://www.linkedin.com/jobs/view/{linkedin_id}", title=title,
                      company_name=company.name, company_id=company.id, keyword_matched="llm remote", track="ml_ai",
                      raw_text=f"{title} posting text")
            db.add(job)
            db.flush()
            db.add_all([JobSkill(job_id=job.id, skill_name=s) for s in skills])
            db.add(JobSeniority(job_id=job.id, level=level))
            jobs[linkedin_id] = job.id
        dup = Job(job_id="4", url="u4", title="ML Engineer", company_name="Acme", company_id=acme.id,
                  keyword_matched="llm remote", track="ml_ai", raw_text="ML Engineer posting text", duplicate_of_job_id=jobs["1"])
        ag = Job(job_id="5", url="u5", title="AI Engineer", company_name="MeeBoss", company_id=agency.id,
                 keyword_matched="llm remote", track="ml_ai", raw_text="on behalf of the hiring company")
        db.add_all([dup, ag, User(email="owner@example.com", role="admin")])
    return jobs


@pytest.fixture
def client(app_url, admin_url, board, tmp_path):  # noqa: F811
    from cloud_api.app import create_app
    from cloud_api.rescore import InlinePublisher
    from cloud_api.auth.verify import FakeVerifier
    from resume.storage import DevSignedStorage
    from resume.store import ResumeCipher

    storage = DevSignedStorage(root=tmp_path / "files", secret=b"test-secret", base_url="http://localhost")
    app = create_app(app_url, admin_database_url=admin_url, verifier=FakeVerifier(), auth_mode="dev",
                     host="127.0.0.1", storage=storage, cipher=ResumeCipher(Fernet.generate_key()),
                     rescore_publisher=InlinePublisher(PG_URL))
    app.config["TESTING"] = True
    app.config["TEST_STORAGE"] = storage
    return app.test_client()


def _resume(client, headers, skills_text):
    started = client.post("/api/v1/me/resume", json={"filename": "cv.txt"}, headers=headers).get_json()
    client.post(urlparse(started["upload"]["url"]).path, content_type="multipart/form-data",
                data={**started["upload"]["fields"], "file": (io.BytesIO(skills_text.encode()), "cv.txt")})
    done = client.post(f"/api/v1/me/resume/{started['version']}/complete", headers=headers).get_json()
    client.put(f"/api/v1/me/resume/{started['resume_id']}/skills", json={"skills": done["skills_extracted"]}, headers=headers)
    return started


def _onboard(client, headers, skills_text, levels):
    """`levels` is one level name or a list of up to three: the route always
    takes a list, and most of these tests only care about one."""
    client.put("/api/v1/me/profile/level",
               json={"levels": [levels] if isinstance(levels, str) else list(levels)}, headers=headers)
    _resume(client, headers, skills_text)


def _jobs(client, headers):
    response = client.get("/api/v1/jobs", headers=headers)
    assert response.status_code == 200, response.get_json()
    return {j["job_id"]: j for j in response.get_json()["jobs"]}


@needs_pg
class TestRoles:
    def _as(self, url):
        return create_engine(url, connect_args={"options": "-c timezone=UTC"})

    def test_user_role_reads_the_board_but_cannot_write_it(self, app_url, board):
        engine = self._as(app_url)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM jobs")).scalar() == 5
        for statement in ("UPDATE jobs SET title = 'x'", "INSERT INTO companies (name) VALUES ('Evil')",
                          "DELETE FROM job_seniority", "SELECT count(*) FROM api_tokens",
                          "SELECT count(*) FROM screening_results"):
            with engine.connect() as conn, pytest.raises(DBAPIError):
                conn.execute(text(statement))
        engine.dispose()

    def test_user_role_cannot_create_an_admin_or_change_another_user(self, app_url, board):
        from cloud_api.user_data import set_request_user

        engine = self._as(app_url)
        with Session(engine) as db, pytest.raises(DBAPIError):
            db.execute(text("INSERT INTO users (email, role, created_at) VALUES ('x@example.com', 'admin', now())"))
        with Session(engine) as db, db.begin():
            owner_id = db.execute(text("SELECT id FROM users WHERE email = 'owner@example.com'")).scalar()
            db.execute(text("INSERT INTO users (email, role, created_at) VALUES ('me@example.com', 'user', now())"))
            me = db.execute(text("SELECT id FROM users WHERE email = 'me@example.com'")).scalar()
            set_request_user(db, me)
            assert db.execute(text("UPDATE users SET display_name = 'hacked' WHERE id = :id"), {"id": owner_id}).rowcount == 0
        with Session(engine) as db, pytest.raises(DBAPIError):
            db.execute(text("UPDATE users SET role = 'admin'"))
        engine.dispose()

    def test_admin_role_cannot_read_per_user_tables(self, admin_url, board):
        engine = self._as(admin_url)
        for table in ("resumes", "user_profiles", "user_job_scores", "job_tracking", "application_events"):
            with engine.connect() as conn, pytest.raises(DBAPIError):
                conn.execute(text(f"SELECT count(*) FROM {table}"))
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM api_tokens")).scalar() == 0
        engine.dispose()


@needs_pg
class TestSkillsVocabulary:
    """The Skills step only accepts canonical taxonomy names, so the client has
    to know them; this is where its type-ahead gets them."""

    def test_every_canonical_name_is_returned_with_the_taxonomy_fingerprint(self, client):
        from analysis.skills_extractor import SKILL_TAXONOMY
        from analysis.taxonomy_version import taxonomy_version

        body = client.get("/api/v1/skills", headers=A).get_json()
        assert body["skills"] == sorted(SKILL_TAXONOMY) == sorted(body["skills"])
        assert body["taxonomy_version"] == taxonomy_version()

    def test_the_names_are_exactly_the_ones_the_skills_step_accepts(self, client):
        """Whatever the type-ahead offers must survive confirmation."""
        _resume(client, A, "SKILLS\nPython\n")
        resume_id = client.get("/api/v1/me/resume", headers=A).get_json()["versions"][0]["id"]
        offered = client.get("/api/v1/skills", headers=A).get_json()["skills"]
        response = client.put(f"/api/v1/me/resume/{resume_id}/skills",
                              json={"skills": offered[:5]}, headers=A)
        assert response.status_code == 200 and response.get_json()["skills_confirmed"] == sorted(offered[:5])

    def test_it_needs_a_signed_in_caller(self, client):
        assert client.get("/api/v1/skills").status_code == 401


@needs_pg
class TestProfile:
    def test_pick_levels_then_confirm_the_score_table(self, client):
        assert client.get("/api/v1/me/profile", headers=A).get_json()["profile"] is None
        picked = client.put("/api/v1/me/profile/level", json={"levels": ["senior", "mid_senior"]},
                            headers=A).get_json()
        # Stored and returned in level order, whichever order the user clicked in.
        assert picked["seniority_targets"] == ["mid_senior", "senior"] and picked["seniority_scores"] is None
        assert picked["proposed_scores"]["mid_senior"] == picked["proposed_scores"]["senior"] == 5
        assert client.get("/api/v1/me/profile", headers=A).get_json()["profile"]["seniority_targets"] == \
            ["mid_senior", "senior"]
        table = {**picked["proposed_scores"], "entry": 5}
        confirmed = client.put("/api/v1/me/profile/scores", json={"scores": table}, headers=A).get_json()
        assert confirmed["seniority_scores"] == table
        assert client.get("/api/v1/me", headers=A).get_json()["onboarding"]["scores_confirmed"] is True

    @pytest.mark.parametrize("body", [
        {"levels": ["junior"]},                                          # not a level
        {"levels": []},                                                  # no pick at all
        {"levels": ["intern", "entry", "mid_senior", "senior"]},          # four picks
        {"levels": ["entry", "entry"]},                                  # the same level twice
        {"levels": "entry"},                                             # not a list
        {"level": "entry"},                                              # the old single-level shape
        {},
    ])
    def test_a_bad_list_of_levels_is_400_with_a_readable_message(self, client, body):
        response = client.put("/api/v1/me/profile/level", json=body, headers=A)
        assert response.status_code == 400
        assert response.get_json()["error"] and "Traceback" not in response.get_json()["error"]
        assert client.get("/api/v1/me/profile", headers=A).get_json()["profile"] is None

    def test_a_bad_score_table_is_400(self, client):
        client.put("/api/v1/me/profile/level", json={"levels": ["entry"]}, headers=A)
        assert client.put("/api/v1/me/profile/scores", json={"scores": {"entry": 9}}, headers=A).status_code == 400


@needs_pg
class TestJobsBoard:
    def test_each_user_sees_their_own_scores_on_the_same_jobs(self, client):
        _onboard(client, A, "SKILLS\nPython, SQL\n", "entry")
        _onboard(client, B, "SKILLS\nKubernetes\n", "staff_principal")
        a, b = _jobs(client, A), _jobs(client, B)
        assert set(a) == set(b) == {"1", "2", "3"}             # the duplicate and the agency posting are not on the board
        assert (a["1"]["scores"]["seniority_fit"], b["1"]["scores"]["seniority_fit"]) == (5, 2)
        assert a["1"]["scores"]["skill_score"] > b["1"]["scores"]["skill_score"]

    def test_the_board_never_returns_raw_text_or_other_users_tracking(self, client, board):
        _onboard(client, A, "SKILLS\nPython\n", "entry")
        client.put(f"/api/v1/me/tracking/{board['1']}", json={"applied": True, "note": "a's secret"}, headers=A)
        b = _jobs(client, B)
        assert all("raw_text" not in job for job in b.values())
        assert b["1"]["tracking"] is None

    def test_company_applied_count_is_per_user(self, client, board):
        for job in ("1", "2"):
            client.put(f"/api/v1/me/tracking/{board[job]}", json={"applied": True}, headers=A)
        assert _jobs(client, A)["3"]["company_applied_count"] == 0
        assert _jobs(client, A)["1"]["company_applied_count"] == 2
        assert _jobs(client, B)["1"]["company_applied_count"] == 0


@needs_pg
class TestBoardWindow:
    def test_days_keeps_only_recently_collected_postings(self, client, board, pg_engine):  # noqa: F811
        from datetime import datetime

        from db.models import Job

        _onboard(client, A, "SKILLS\nPython, SQL\n", "entry")
        with Session(pg_engine) as db, db.begin():   # collected long before the window
            db.get(Job, board["3"]).first_seen_at = datetime(2026, 1, 2)

        recent = client.get("/api/v1/jobs?days=14", headers=A).get_json()
        everything = client.get("/api/v1/jobs", headers=A).get_json()

        assert recent["days"] == 14 and everything["days"] is None
        assert {job["job_id"] for job in recent["jobs"]} == {"1", "2"}
        assert {job["job_id"] for job in everything["jobs"]} == {"1", "2", "3"}


@needs_pg
class TestTrackingAndApplications:
    def test_tracking_is_private_and_applied_records_an_event(self, client, board):
        saved = client.put(f"/api/v1/me/tracking/{board['1']}", json={"applied": True, "note": "a's note"}, headers=A)
        assert saved.status_code == 200 and saved.get_json()["applied_at"] is not None
        apps = client.get("/api/v1/me/applications", headers=A).get_json()["applications"]
        assert [(x["job_id"], [e["stage"] for e in x["events"]]) for x in apps] == [(board["1"], ["applied"])]
        assert client.get("/api/v1/me/applications", headers=B).get_json()["applications"] == []

    def test_b_writing_the_same_job_changes_only_bs_row(self, client, board, pg_engine):  # noqa: F811
        from db.cloud_models import JobTracking

        client.put(f"/api/v1/me/tracking/{board['1']}", json={"note": "a's note"}, headers=A)
        client.put(f"/api/v1/me/tracking/{board['1']}", json={"note": "b's note"}, headers=B)
        with Session(pg_engine) as db:
            assert sorted(t.note for t in db.query(JobTracking)) == ["a's note", "b's note"]

    def test_later_stages_and_bad_input(self, client, board):
        client.put(f"/api/v1/me/tracking/{board['1']}", json={"applied": True}, headers=A)
        added = client.post(f"/api/v1/me/applications/{board['1']}/events",
                            json={"stage": "interview", "occurred_at": "2026-09-20T15:00:00", "note": "round 1"}, headers=A)
        assert added.status_code == 201
        stages = client.get("/api/v1/me/applications", headers=A).get_json()["applications"][0]["events"]
        assert [e["stage"] for e in stages] == ["applied", "interview"]
        assert client.post(f"/api/v1/me/applications/{board['1']}/events", json={"stage": "ghosted"}, headers=A).status_code == 400
        assert client.put("/api/v1/me/tracking/999999", json={"applied": True}, headers=A).status_code == 404


@needs_pg
class TestAccountDeletion:
    def test_deleting_an_account_removes_every_row_and_file(self, client, board, pg_engine):  # noqa: F811
        """Every cloud table with a user_id column, found by walking the models, so a
        per-user table added later is covered without anyone remembering it."""
        from db.cloud_models import CloudBase, User

        _onboard(client, A, "SKILLS\nPython\n", "entry")
        client.put(f"/api/v1/me/tracking/{board['1']}", json={"applied": True}, headers=A)
        _onboard(client, B, "SKILLS\nSQL\n", "entry")
        a_id = client.get("/api/v1/me", headers=A).get_json()["id"]
        storage = client.application.config["TEST_STORAGE"]
        assert storage.files._path(f"users/{a_id}").exists()
        per_user = [t for t in CloudBase.metadata.sorted_tables if "user_id" in t.c]
        assert {"resumes", "user_profiles", "user_job_scores", "job_tracking", "application_events",
                "expertise_profiles", "user_job_expertise", "api_tokens"} <= {t.name for t in per_user}

        assert client.delete("/api/v1/me", headers=A).status_code == 204

        with Session(pg_engine) as db:
            assert db.get(User, a_id) is None
            leftovers = {t.name: db.execute(text(f"SELECT count(*) FROM {t.name} WHERE user_id = :u"), {"u": a_id}).scalar()
                         for t in per_user}
            assert leftovers == dict.fromkeys(leftovers, 0)
        assert not storage.files._path(f"users/{a_id}").exists()
        assert _jobs(client, B)["1"]["scores"] is not None
