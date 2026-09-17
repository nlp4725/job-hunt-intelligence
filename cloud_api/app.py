"""The cloud API (productization plan §6), built alongside the local app in
backend/, which stays unchanged.

    create_app(database_url, verifier=CognitoVerifier(...))                          # production
    create_app(database_url, verifier=FakeVerifier(), auth_mode="dev", host="127.0.0.1")  # local

One database session per request: committed when the request succeeds,
rolled back when it raises. Rescore messages queued during the request are
published only after the commit (cloud_api/rescore.py). Every request writes
one structured log line; unhandled errors and database permission errors are
logged with a request id and counted (cloud_api/observability.py).
"""

import time
import traceback
import uuid

from flask import Flask, current_app, g, jsonify, request
from flask_cors import CORS
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import HTTPException

from cloud_api.auth.decorators import require_admin, require_admin_person, require_collector, require_user
from cloud_api.auth.tokens import create_api_token, revoke_api_token
from cloud_api.auth.verify import FakeVerifier
from cloud_api import admin_data, user_data
from cloud_api.dev_storage import dev_storage
from cloud_api.observability import Timer, log_event, metric
from cloud_api.rescore import job_message, queue_after_commit, user_message
from cloud_api.user_data import onboarding_state
from resume.resume_text import UnsupportedResume
from resume.storage import URL_EXPIRES_SECONDS

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _classify_with_deepseek(posting: str):
    from judge.seniority_level import classify_job_seniority

    return classify_job_seniority(posting)


def _draft_with_deepseek(resume_text: str) -> dict:
    from judge.expertise_profile import draft_expertise_profile

    return draft_expertise_profile(resume_text)


def create_app(database_url: str, verifier, *, auth_mode: str = "cognito", host: str = "127.0.0.1",
               cors_origins: tuple[str, ...] = (), storage=None, cipher=None,
               admin_database_url: str | None = None, classify=None, draft_expertise=None,
               rescore_publisher=None) -> Flask:
    """database_url: a login in the jhi_app role (user requests).
    admin_database_url: a login in the jhi_admin_api role (admin routes);
    defaults to database_url for single-login local setups.
    rescore_publisher: SqsPublisher in production, InlinePublisher locally;
    None drops messages (the hourly reconciliation still scores everything)."""
    if auth_mode == "dev" and host not in LOCAL_HOSTS:
        raise RuntimeError("dev login (FakeVerifier) is only allowed when the server is bound to localhost")
    if auth_mode != "dev" and isinstance(verifier, FakeVerifier):
        raise RuntimeError("FakeVerifier requires auth_mode='dev'")

    def engine_for(url):
        return create_engine(url, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"})

    user_engine = engine_for(database_url)
    admin_engine = engine_for(admin_database_url) if admin_database_url else user_engine

    app = Flask(__name__)
    app.config["VERIFIER"] = verifier
    app.config["CLASSIFY"] = classify or _classify_with_deepseek   # job seniority level, once per captured job
    app.config["DRAFT_EXPERTISE"] = draft_expertise or _draft_with_deepseek   # paid: expertise profile draft
    app.config["USER_SESSION"] = sessionmaker(bind=user_engine)     # opened by require_user
    app.config["ADMIN_SESSION"] = sessionmaker(bind=admin_engine)   # opened by require_admin
    app.config["STORAGE"] = storage     # resume.storage.S3ResumeStorage, or DevSignedStorage in dev
    app.config["CIPHER"] = cipher       # resume.store.ResumeCipher for extracted resume text
    app.config["RESCORE_PUBLISHER"] = rescore_publisher
    if auth_mode == "dev":
        app.register_blueprint(dev_storage)
    if cors_origins:
        CORS(app, origins=list(cors_origins))

    @app.before_request
    def start_request():
        g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        g.started = time.perf_counter()

    @app.after_request
    def log_request(response):
        user = g.get("user")
        log_event("request", request_id=g.get("request_id"), method=request.method,
                  route=request.url_rule.rule if request.url_rule else "unmatched", status=response.status_code,
                  duration_ms=round((time.perf_counter() - g.get("started", time.perf_counter())) * 1000, 1),
                  user_id=user.id if user is not None else None, token_scope=g.get("token_scope"))
        response.headers["X-Request-Id"] = g.get("request_id", "")
        return response

    @app.errorhandler(Exception)
    def unhandled(exc):
        if isinstance(exc, HTTPException):
            return exc
        g.failed = True   # roll back: a handled exception doesn't reach teardown as `exc`
        if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) == "42501":
            metric("DbPermissionDenied")
            log_event("db_permission_denied", level="error", request_id=g.get("request_id"), error=str(exc.orig).splitlines()[0])
        else:
            metric("ApiUnhandledError")
            log_event("unhandled_error", level="error", request_id=g.get("request_id"), error_type=type(exc).__name__,
                      stack=traceback.format_exc(limit=20))
        return jsonify({"error": "internal error", "request_id": g.get("request_id")}), 500

    @app.teardown_request
    def close_session(exc):
        db = g.pop("db", None)
        messages = g.pop("rescore_messages", [])
        committed = False
        if db is not None:
            try:
                if exc is None and not g.get("failed"):
                    db.commit()
                    committed = True
                else:
                    db.rollback()
            finally:
                db.close()
        if committed and messages:
            publish_rescores(messages)

    def publish_rescores(messages):
        publisher = app.config["RESCORE_PUBLISHER"]
        if publisher is None:
            log_event("rescore_not_published", level="warning", count=len(messages), reason="no publisher")
            return
        try:
            publisher.publish(messages)
            metric("RescorePublished", len(messages))
        except Exception as exc:   # the request already succeeded; reconciliation catches up
            metric("RescorePublishFailed", len(messages))
            log_event("rescore_publish_failed", level="error", count=len(messages), error_type=type(exc).__name__, error=str(exc)[:300])

    @app.get("/healthz")
    def healthz():
        """Load balancer health check. Public and deliberately free of the
        database, so a database blip doesn't make ECS replace healthy tasks."""
        return jsonify({"ok": True})

    @app.get("/api/public/stats")
    def public_stats():
        """The landing page's numbers. No sign-in, aggregates only, cached in
        the task for a few minutes so a burst of visitors can't load the database."""
        cached = app.config.get("PUBLIC_STATS")
        now = time.monotonic()
        if cached and now - cached[0] < admin_data.PUBLIC_STATS_TTL:
            return jsonify(cached[1])
        db = current_app.config["ADMIN_SESSION"]()
        try:
            stats = admin_data.public_stats(db)
        finally:
            db.close()
        app.config["PUBLIC_STATS"] = (now, stats)
        return jsonify(stats)

    @app.get("/api/public/recent-jobs")
    def public_recent_jobs():
        """A sample of the real board for the landing page: recent postings,
        public facts only. Agency, duplicate and expired jobs are left out."""
        cached = app.config.get("PUBLIC_RECENT")
        now = time.monotonic()
        if cached and now - cached[0] < admin_data.PUBLIC_STATS_TTL:
            return jsonify({"jobs": cached[1]})
        db = current_app.config["ADMIN_SESSION"]()
        try:
            jobs = admin_data.public_recent_jobs(db)
        finally:
            db.close()
        app.config["PUBLIC_RECENT"] = (now, jobs)
        return jsonify({"jobs": jobs})

    @app.get("/api/v1/me")
    @require_user
    def me():
        user = g.user
        return jsonify({
            "id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role, "plan": user.plan,
            "onboarding": onboarding_state(g.db, user),
        })

    def error(status: int, message: str):
        return jsonify({"error": message}), status

    @app.post("/api/v1/me/resume")
    @require_user
    def start_resume_upload():
        filename = str((request.get_json(silent=True) or {}).get("filename", ""))
        try:
            row, ticket = user_data.start_resume_upload(g.db, g.user, filename, app.config["STORAGE"])
        except UnsupportedResume as exc:
            return error(415, str(exc))
        return jsonify({"resume_id": row.id, "version": row.version,
                        "upload": {"url": ticket.url, "fields": ticket.fields, "expires_in": ticket.expires_in}}), 201

    @app.post("/api/v1/me/resume/<int:version>/complete")
    @require_user
    def finish_resume_upload(version: int):
        try:
            with Timer() as timer:
                resume = user_data.finish_resume_upload(g.db, g.user, version, app.config["STORAGE"], app.config["CIPHER"])
        except LookupError:
            return error(404, "resume not found")
        except FileNotFoundError:
            return error(409, "the file has not been uploaded yet")
        except UnsupportedResume as exc:
            metric("ResumeParseFailed")
            log_event("resume_parse_failed", level="warning", user_id=g.user.id, reason=type(exc).__name__)
            return error(415, str(exc))
        metric("ResumeProcessed")
        metric("ResumeProcessingMs", timer.ms, unit="Milliseconds")
        return jsonify(resume)

    @app.get("/api/v1/me/resume")
    @require_user
    def list_resumes():
        return jsonify(user_data.list_resumes(g.db, g.user))

    @app.put("/api/v1/me/resume/<int:resume_id>/skills")
    @require_user
    def confirm_resume_skills(resume_id: int):
        skills = (request.get_json(silent=True) or {}).get("skills")
        if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
            return error(400, "skills must be a list of skill names")
        try:
            resume = user_data.confirm_resume_skills(g.db, g.user, resume_id, skills)
            queue_after_commit(user_message(g.user.id))
            return jsonify(resume)
        except LookupError:
            return error(404, "resume not found")
        except ValueError as exc:
            return error(400, str(exc))

    @app.get("/api/v1/me/resume/<int:resume_id>/file")
    @require_user
    def resume_download_link(resume_id: int):
        try:
            url = user_data.resume_download_link(g.db, g.user, resume_id, app.config["STORAGE"])
        except LookupError:
            return error(404, "resume not found")
        return jsonify({"url": url, "expires_in": URL_EXPIRES_SECONDS})

    @app.get("/api/v1/me/profile")
    @require_user
    def get_profile():
        return jsonify(user_data.get_profile(g.db, g.user))

    @app.put("/api/v1/me/profile/level")
    @require_user
    def pick_level():
        try:
            profile = user_data.pick_level(g.db, g.user, (request.get_json(silent=True) or {}).get("level"))
            queue_after_commit(user_message(g.user.id))
            return jsonify(profile)
        except ValueError as exc:
            return error(400, str(exc))

    @app.put("/api/v1/me/profile/scores")
    @require_user
    def confirm_scores():
        try:
            profile = user_data.confirm_scores(g.db, g.user, (request.get_json(silent=True) or {}).get("scores"))
            queue_after_commit(user_message(g.user.id))
            return jsonify(profile)
        except ValueError as exc:
            return error(400, str(exc))

    @app.get("/api/v1/me/expertise")
    @require_user
    def get_expertise():
        return jsonify(user_data.get_expertise(g.db, g.user))

    @app.post("/api/v1/me/expertise/draft")
    @require_user
    def draft_expertise_profile():
        def timed_drafter(resume_text):
            try:
                with Timer() as timer:
                    draft = app.config["DRAFT_EXPERTISE"](resume_text)
            except Exception:
                metric("ExpertiseDraftFailed")
                raise
            metric("ExpertiseDraftMs", timer.ms, unit="Milliseconds")
            return draft

        try:
            return jsonify(user_data.draft_expertise(g.db, g.user, timed_drafter)), 201
        except user_data.PaidFeature as exc:
            return error(402, str(exc))
        except user_data.TooManyDrafts as exc:
            return error(429, str(exc))
        except ValueError as exc:
            return error(409, str(exc))

    @app.put("/api/v1/me/expertise")
    @require_user
    def save_expertise():
        try:
            return jsonify(user_data.save_expertise(g.db, g.user, request.get_json(silent=True)))
        except user_data.PaidFeature as exc:
            return error(402, str(exc))
        except ValueError as exc:
            return error(400, str(exc))

    @app.post("/api/v1/me/expertise/skip")
    @require_user
    def skip_expertise():
        return jsonify(user_data.skip_expertise(g.db, g.user))

    @app.get("/api/v1/jobs")
    @require_user
    def job_board():
        limit = min(max(request.args.get("limit", 100, type=int), 1), 200)
        offset = max(request.args.get("offset", 0, type=int), 0)
        days = request.args.get("days", type=int)           # omit for the whole corpus
        days = min(max(days, 1), 3650) if days else None
        jobs = user_data.job_board(g.db, g.user, limit, offset, days=days)
        return jsonify({"jobs": jobs, "limit": limit, "offset": offset, "days": days})

    @app.put("/api/v1/me/tracking/<int:job_id>")
    @require_user
    def save_tracking(job_id: int):
        changes = request.get_json(silent=True)
        if not isinstance(changes, dict):
            return error(400, "a JSON object is required")
        try:
            return jsonify(user_data.save_tracking(g.db, g.user, job_id, changes))
        except LookupError:
            return error(404, "job not found")
        except ValueError as exc:
            return error(400, str(exc))

    @app.post("/api/v1/me/applications/<int:job_id>/events")
    @require_user
    def add_application_event(job_id: int):
        body = request.get_json(silent=True) or {}
        try:
            event = user_data.add_application_event(g.db, g.user, job_id, body.get("stage"),
                                                    body.get("occurred_at"), body.get("note"))
        except LookupError:
            return error(404, "job not found")
        except ValueError as exc:
            return error(400, str(exc))
        return jsonify(event), 201

    @app.get("/api/v1/me/applications")
    @require_user
    def list_applications():
        return jsonify({"applications": user_data.list_applications(g.db, g.user)})

    @app.delete("/api/v1/me")
    @require_user
    def delete_account():
        user_data.delete_account(g.db, g.user, app.config["STORAGE"])
        return "", 204

    @app.post("/api/v1/admin/captures")
    @require_collector
    def capture():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            metric("CaptureOutcome", dimensions={"Outcome": "invalid"})
            return error(400, "a JSON object is required")
        try:
            result = admin_data.capture(g.db, body, app.config["CLASSIFY"])
        except ValueError as exc:
            metric("CaptureOutcome", dimensions={"Outcome": "invalid"})
            return error(400, str(exc))
        metric("CaptureOutcome", dimensions={"Outcome": result["status"]})
        if result["status"] in ("scored", "saved"):
            queue_after_commit(job_message(result["job"]["id"]))
        return jsonify(result)

    @app.get("/api/v1/admin/captures/<linkedin_id>")
    @require_collector
    def cached_capture(linkedin_id: str):
        cached = admin_data.cached_capture(g.db, linkedin_id)
        return jsonify(cached) if cached else error(404, "not captured yet")

    @app.get("/api/v1/admin/agencies")
    @require_collector
    def agencies():
        return jsonify(admin_data.agency_list())

    @app.post("/api/v1/admin/collection-pages")
    @require_collector
    def collection_page():
        try:
            return jsonify(admin_data.record_collection_page(g.db, request.get_json(silent=True) or {})), 201
        except ValueError as exc:
            return error(400, str(exc))

    @app.patch("/api/v1/admin/jobs/<int:job_id>")
    @require_collector
    def expire_job(job_id: int):
        try:
            return jsonify(admin_data.set_expired(g.db, job_id, (request.get_json(silent=True) or {}).get("expired")))
        except LookupError:
            return error(404, "job not found")
        except ValueError as exc:
            return error(400, str(exc))

    @app.put("/api/v1/admin/plans")
    @require_admin
    def set_plan():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(admin_data.set_plan(g.db, body.get("email"), body.get("plan")))
        except LookupError:
            return error(404, "no such account")
        except ValueError as exc:
            return error(400, str(exc))

    @app.post("/api/v1/admin/tokens")
    @require_admin_person
    def create_token():
        body = request.get_json(silent=True) or {}
        label = str(body.get("label", "")).strip()[:100]
        if not label:
            return jsonify({"error": "label required"}), 400
        try:
            row, plaintext = create_api_token(g.db, g.user, label, scope=body.get("scope", "collector"))
        except ValueError as exc:
            return error(400, str(exc))
        log_event("api_token_created", user_id=g.user.id, token_id=row.id, scope=row.scope)
        return jsonify({"id": row.id, "label": row.label, "scope": row.scope, "token": plaintext}), 201

    @app.delete("/api/v1/admin/tokens/<int:token_id>")
    @require_admin_person
    def revoke_token(token_id: int):
        if not revoke_api_token(g.db, g.user, token_id):
            return jsonify({"error": "no such token"}), 404
        return "", 204

    return app
