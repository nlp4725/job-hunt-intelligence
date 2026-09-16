"""Paid tier: Expertise Match (productization plan §3.4).

Expertise is a paid-member feature and an optional onboarding step. Free
members see it locked and may skip it; paid members draft a profile (one LLM
call), edit and confirm it, or skip. It never holds up onboarding. A member
cannot make themselves paid: plans change only through the admin route. The
owner-run worker scores board jobs for paid members; users only read scores.

Postgres tests need JHI_TEST_POSTGRES_URL. The drafter and scorer are fakes: no LLM.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests_and_eval.test_cloud_db import PG_URL, needs_pg, pg_engine  # noqa: F401  (fixture)
from tests_and_eval.test_cloud_isolation import admin_url, app_url  # noqa: F401  (fixtures)
from tests_and_eval.test_cloud_user_routes import A, B, OWNER, _jobs, _onboard, board  # noqa: F401  (fixture)

PROFILE = {"summary": "Career changer from science into AI.", "main_work": ["Built an LLM screening pipeline"],
           "dream": "AI in healthcare"}


class FakeDrafter:
    def __init__(self):
        self.calls = []

    def __call__(self, resume_text):
        self.calls.append(resume_text)
        return {"summary": "Drafted summary", "main_work": ["Drafted line", "  "], "dream": ""}


@pytest.fixture
def drafter():
    return FakeDrafter()


@pytest.fixture
def client(app_url, admin_url, board, drafter, tmp_path):  # noqa: F811
    from cryptography.fernet import Fernet

    from cloud_api.app import create_app
    from cloud_api.auth.verify import FakeVerifier
    from resume.storage import DevSignedStorage
    from resume.store import ResumeCipher

    app = create_app(app_url, admin_database_url=admin_url, verifier=FakeVerifier(), auth_mode="dev", host="127.0.0.1",
                     storage=DevSignedStorage(root=tmp_path / "files", secret=b"s", base_url="http://localhost"),
                     cipher=ResumeCipher(Fernet.generate_key()), draft_expertise=drafter)
    app.config["TESTING"] = True
    return app.test_client()


def _make_paid(client, email="a@example.com"):
    response = client.put("/api/v1/admin/plans", json={"email": email, "plan": "paid"}, headers=OWNER)
    assert response.status_code == 200, response.get_json()


def _onboarded(client, headers=A):
    _onboard(client, headers, "SKILLS\nPython, SQL\n", "entry")
    client.put("/api/v1/me/profile/scores", headers=headers,
               json={"scores": client.get("/api/v1/me/profile", headers=headers).get_json()["profile"]["proposed_scores"]})


@needs_pg
class TestOnboardingStep:
    def test_free_members_see_it_locked_and_can_skip_without_blocking_onboarding(self, client):
        _onboarded(client)
        state = client.get("/api/v1/me", headers=A).get_json()
        assert state["plan"] == "free"
        assert state["onboarding"]["complete"] is True and state["onboarding"]["expertise"] == "locked"
        assert client.post("/api/v1/me/expertise/skip", headers=A).get_json() == {"step": "skipped"}
        assert client.get("/api/v1/me", headers=A).get_json()["onboarding"]["expertise"] == "skipped"

    def test_free_members_cannot_draft_or_save(self, client, drafter):
        _onboarded(client)
        assert client.post("/api/v1/me/expertise/draft", headers=A).status_code == 402
        assert client.put("/api/v1/me/expertise", json=PROFILE, headers=A).status_code == 402
        assert drafter.calls == []

    def test_paid_members_draft_edit_and_confirm(self, client, drafter):
        _onboarded(client)
        _make_paid(client)
        assert client.get("/api/v1/me", headers=A).get_json()["onboarding"]["expertise"] == "pending"

        draft = client.post("/api/v1/me/expertise/draft", headers=A)
        assert draft.status_code == 201
        assert draft.get_json() | {"created_at": None} == {"version": 1, "summary": "Drafted summary",
                                                           "main_work": ["Drafted line"], "dream": "",
                                                           "confirmed": False, "created_at": None}
        assert len(drafter.calls) == 1

        saved = client.put("/api/v1/me/expertise", json=PROFILE, headers=A).get_json()
        assert (saved["version"], saved["confirmed"], saved["dream"]) == (2, True, "AI in healthcare")
        view = client.get("/api/v1/me/expertise", headers=A).get_json()
        assert (view["step"], view["profile"]["version"], view["draft"]) == ("done", 2, None)

    def test_saving_after_a_skip_counts_as_done(self, client):
        _onboarded(client)
        _make_paid(client)
        client.post("/api/v1/me/expertise/skip", headers=A)
        client.put("/api/v1/me/expertise", json=PROFILE, headers=A)
        assert client.get("/api/v1/me", headers=A).get_json()["onboarding"]["expertise"] == "done"

    def test_drafting_needs_a_confirmed_resume_and_is_rate_limited(self, client, drafter):
        client.get("/api/v1/me", headers=A)
        _make_paid(client)
        assert client.post("/api/v1/me/expertise/draft", headers=A).status_code == 409
        _onboarded(client)
        codes = [client.post("/api/v1/me/expertise/draft", headers=A).status_code for _ in range(4)]
        assert codes == [201, 201, 201, 429] and len(drafter.calls) == 3

    def test_invalid_profiles_are_400(self, client):
        _onboarded(client)
        _make_paid(client)
        for body in ({"summary": "s", "main_work": []}, {**PROFILE, "main_work": []}, {**PROFILE, "summary": " "},
                     {**PROFILE, "main_work": ["x"] * 9}, {**PROFILE, "dream": "d" * 601}, {**PROFILE, "avoid": ""}):
            assert client.put("/api/v1/me/expertise", json=body, headers=A).status_code == 400, body


@needs_pg
class TestPlans:
    def test_only_admins_set_plans(self, client):
        client.get("/api/v1/me", headers=A)
        assert client.put("/api/v1/admin/plans", json={"email": "a@example.com", "plan": "paid"}, headers=A).status_code == 403
        assert client.put("/api/v1/admin/plans", json={"email": "nobody@example.com", "plan": "paid"}, headers=OWNER).status_code == 404
        assert client.put("/api/v1/admin/plans", json={"email": "a@example.com", "plan": "gold"}, headers=OWNER).status_code == 400
        _make_paid(client, "A@example.com")
        assert client.get("/api/v1/me", headers=A).get_json()["plan"] == "paid"

    def test_the_user_role_cannot_make_itself_paid(self, app_url, board):  # noqa: F811
        from cloud_api.user_data import set_request_user

        engine = create_engine(app_url)
        with Session(engine) as db, pytest.raises(DBAPIError):
            db.execute(text("INSERT INTO users (email, role, plan, created_at) VALUES ('x@example.com', 'user', 'paid', now())"))
        with Session(engine) as db, pytest.raises(DBAPIError):
            me = db.execute(text("SELECT id FROM users WHERE email = 'owner@example.com'")).scalar()
            set_request_user(db, me)
            db.execute(text("UPDATE users SET plan = 'paid' WHERE id = :id"), {"id": me})
        with Session(engine) as db, pytest.raises(DBAPIError):
            db.execute(text("SELECT set_user_plan('owner@example.com', 'paid')"))
        engine.dispose()

    def test_the_user_role_cannot_write_expertise_scores(self, app_url, board):  # noqa: F811
        from cloud_api.user_data import set_request_user

        engine = create_engine(app_url)
        with Session(engine) as db, pytest.raises(DBAPIError):
            me = db.execute(text("SELECT id FROM users WHERE email = 'owner@example.com'")).scalar()
            set_request_user(db, me)
            db.execute(text("INSERT INTO user_job_expertise (user_id, job_id, profile_version, domain_score, "
                            "capability_score, dream_score, expertise_score, scored_at) "
                            "VALUES (:me, :job, 1, 5, 5, 5, 5.0, now())"), {"me": me, "job": board["1"]})
        engine.dispose()


def _fake_scorer(calls):
    def score(resume_text, profile, posting_text):
        calls.append((resume_text, profile["dream"], posting_text.splitlines()[0]))
        match = SimpleNamespace(domain_score=2, capability_score=4, dream_score=5, domain_evidence="d",
                                capability_evidence="c", dream_evidence="w", note=None)
        return match, 3.9
    return score


@needs_pg
class TestExpertiseWorker:
    def test_scores_paid_members_only_and_the_board_shows_it_beside_the_total(self, client, board):  # noqa: F811
        from cloud_api.expertise_worker import run_expertise

        _onboarded(client, A)
        _onboarded(client, B)
        _make_paid(client)
        client.put("/api/v1/me/expertise", json=PROFILE, headers=A)

        calls = []
        report = run_expertise(PG_URL, scorer=_fake_scorer(calls), limit=2)
        assert (report.users, report.scored, report.failed) == (1, 2, 0)
        assert all(dream == "AI in healthcare" for _, dream, _ in calls)
        assert not any("MeeBoss" in first for _, _, first in calls)       # agency postings are never sent

        jobs = _jobs(client, A)
        scored = [j for j in jobs.values() if j["expertise"]]
        assert len(scored) == 2
        assert scored[0]["expertise"]["expertise_score"] == 3.9 and scored[0]["expertise"]["stale"] is False
        assert scored[0]["scores"]["total_score"] == scored[0]["scores"]["skill_score"] + scored[0]["scores"]["seniority_fit"]
        assert all(j["expertise"] is None for j in _jobs(client, B).values())

        # the next run scores only what is left, then nothing
        assert run_expertise(PG_URL, scorer=_fake_scorer(calls), limit=5).scored == 1
        assert run_expertise(PG_URL, scorer=_fake_scorer(calls), limit=5).scored == 0

    def test_a_new_profile_version_rescores_and_marks_old_scores_stale(self, client, board):  # noqa: F811
        from cloud_api.expertise_worker import run_expertise

        _onboarded(client, A)
        _make_paid(client)
        client.put("/api/v1/me/expertise", json=PROFILE, headers=A)
        run_expertise(PG_URL, scorer=_fake_scorer([]), limit=5)
        client.put("/api/v1/me/expertise", json={**PROFILE, "dream": "fintech"}, headers=A)
        assert all(j["expertise"]["stale"] for j in _jobs(client, A).values())
        assert run_expertise(PG_URL, scorer=_fake_scorer([]), limit=5).scored == 3

    def test_a_failed_call_is_counted_and_does_not_stop_the_run(self, client, board):  # noqa: F811
        from cloud_api.expertise_worker import run_expertise

        _onboarded(client, A)
        _make_paid(client)
        client.put("/api/v1/me/expertise", json=PROFILE, headers=A)
        good = _fake_scorer([])
        attempts = []

        def flaky(*args):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("model unavailable")
            return good(*args)

        report = run_expertise(PG_URL, scorer=flaky, limit=5)
        assert (report.scored, report.failed) == (2, 1)
