"""Observability (productization plan §1.5.15): structured logs, CloudWatch EMF
metrics, request logging, and error signals that must never leak personal data.

Postgres tests need JHI_TEST_POSTGRES_URL.
"""

import json

import pytest
from sqlalchemy import text

from tests_and_eval.test_cloud_captures import _capture, classifier, client  # noqa: F401  (fixtures)
from tests_and_eval.test_cloud_db import needs_pg, pg_engine  # noqa: F401  (fixture)
from tests_and_eval.test_cloud_isolation import admin_url, app_url  # noqa: F401  (fixtures)
from tests_and_eval.test_cloud_user_routes import A, _onboard


def _lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def _metrics(lines, name):
    return [line for line in lines if name in line and "_aws" in line]


class TestFormats:
    def test_metric_lines_are_valid_embedded_metric_format(self, capsys):
        from cloud_api.observability import metric

        metric("CaptureOutcome", dimensions={"Outcome": "scored"})
        metric("ResumeProcessingMs", 812.5, unit="Milliseconds")
        first, second = _lines(capsys)
        spec = first["_aws"]["CloudWatchMetrics"][0]
        assert spec["Namespace"] == "JHI" and spec["Dimensions"] == [["Outcome"]]
        assert spec["Metrics"] == [{"Name": "CaptureOutcome", "Unit": "Count"}]
        assert (first["CaptureOutcome"], first["Outcome"]) == (1, "scored")
        assert second["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [[]] and second["ResumeProcessingMs"] == 812.5

    def test_logs_drop_fields_that_could_carry_personal_data(self, capsys):
        from cloud_api.observability import log_event

        log_event("x", user_id=4, email="a@example.com", raw_text="posting", token="jhi_abc", Resume="cv")
        (line,) = _lines(capsys)
        assert line["user_id"] == 4 and not {"email", "raw_text", "token", "Resume"} & set(line)


@needs_pg
class TestApiSignals:
    def test_every_request_is_logged_with_id_route_status_and_timing(self, client, capsys):  # noqa: F811
        capsys.readouterr()
        response = client.get("/api/v1/me", headers=A)
        request_logs = [line for line in _lines(capsys) if line.get("event") == "request"]
        assert len(request_logs) == 1
        log = request_logs[0]
        assert log["route"] == "/api/v1/me" and log["status"] == 200 and log["duration_ms"] >= 0 and log["user_id"]
        assert response.headers["X-Request-Id"] == log["request_id"]
        assert "a@example.com" not in json.dumps(log)

    def test_capture_outcomes_and_rescore_publishing_are_counted(self, client, capsys):  # noqa: F811
        capsys.readouterr()
        _capture(client, "500")
        _capture(client, "501", company="MeeBoss")
        lines = _lines(capsys)
        outcomes = [line["Outcome"] for line in _metrics(lines, "CaptureOutcome")]
        assert outcomes == ["scored", "blocked"]
        assert _metrics(lines, "RescorePublished") and _metrics(lines, "SeniorityClassifyMs")

    def test_a_database_permission_error_is_counted_and_rolled_back(self, client, capsys, monkeypatch):  # noqa: F811
        """If a route ever touches a table its role can't, the error is an alarm, not a silent 500."""
        from cloud_api import admin_data

        monkeypatch.setattr(admin_data, "agency_list", lambda: None)

        def reads_user_scores(db, linkedin_id):
            db.execute(text("SELECT count(*) FROM user_job_scores"))   # the admin role has no access

        monkeypatch.setattr(admin_data, "cached_capture", reads_user_scores)
        capsys.readouterr()
        response = client.get("/api/v1/admin/captures/1", headers=client.extension)
        assert response.status_code == 500
        lines = _lines(capsys)
        assert _metrics(lines, "DbPermissionDenied")
        assert [line for line in lines if line.get("event") == "db_permission_denied"]

    def test_resume_parse_failures_are_counted(self, client, capsys):  # noqa: F811
        import io
        from urllib.parse import urlparse

        started = client.post("/api/v1/me/resume", json={"filename": "cv.pdf"}, headers=A).get_json()
        client.post(urlparse(started["upload"]["url"]).path, content_type="multipart/form-data",
                    data={**started["upload"]["fields"], "file": (io.BytesIO(b"not a pdf"), "cv.pdf")})
        capsys.readouterr()
        assert client.post(f"/api/v1/me/resume/{started['version']}/complete", headers=A).status_code == 415
        assert _metrics(_lines(capsys), "ResumeParseFailed")

    def test_board_scored_tells_the_app_when_scoring_has_caught_up(self, client):  # noqa: F811
        from tests_and_eval.test_cloud_captures import RecordingPublisher

        client.application.config["RESCORE_PUBLISHER"] = RecordingPublisher([])   # messages not delivered
        _capture(client, "502")
        _onboard(client, A, "SKILLS\nPython\n", "entry")
        assert client.get("/api/v1/me", headers=A).get_json()["onboarding"]["board_scored"] is False
