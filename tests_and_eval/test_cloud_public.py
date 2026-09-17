"""The landing page's public numbers (productization plan §8).

No sign-in, aggregates only: never a job's text, a company list, or anything
about a user. Postgres tests need JHI_TEST_POSTGRES_URL.
"""

import json

from sqlalchemy.orm import Session

from tests_and_eval.test_cloud_db import needs_pg, pg_engine  # noqa: F401  (fixture)
from tests_and_eval.test_cloud_isolation import admin_url, app_url  # noqa: F401  (fixtures)
from tests_and_eval.test_cloud_user_routes import A, _onboard, board, client  # noqa: F401  (fixtures)


@needs_pg
class TestPublicStats:
    def test_counts_are_public_and_carry_no_user_or_job_text(self, client, board):  # noqa: F811
        response = client.get("/api/public/stats")   # no Authorization header

        assert response.status_code == 200
        stats = response.get_json()
        assert stats["jobs"] == 4          # three board jobs + the agency one; the duplicate is not counted
        assert stats["companies"] == 3 and stats["jobs_with_level"] == 3
        assert stats["last_collected_at"] and stats["collected_today"] == 4
        body = json.dumps(stats).lower()
        assert "posting text" not in body and "acme" not in body and "example.com" not in body

    def test_repeat_calls_are_served_from_the_cache(self, client, pg_engine):  # noqa: F811
        from db.models import Job

        first = client.get("/api/public/stats").get_json()
        with Session(pg_engine) as db, db.begin():
            db.add(Job(job_id="900", url="u900", keyword_matched="llm", track="ml_ai", raw_text="text"))

        assert client.get("/api/public/stats").get_json() == first     # cached for a few minutes
        client.application.config.pop("PUBLIC_STATS")
        assert client.get("/api/public/stats").get_json()["jobs"] == first["jobs"] + 1

    def test_a_signed_in_users_scores_are_not_in_it(self, client, board):  # noqa: F811
        _onboard(client, A, "SKILLS\nPython\n", "entry")
        client.application.config.pop("PUBLIC_STATS", None)

        stats = client.get("/api/public/stats").get_json()

        assert set(stats) == {"jobs", "remote_jobs", "collected_today", "jobs_with_level", "companies", "last_collected_at"}
