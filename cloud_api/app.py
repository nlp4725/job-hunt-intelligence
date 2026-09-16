"""The cloud API (productization plan §6), built alongside the local app in
backend/, which stays unchanged.

    create_app(database_url, verifier=CognitoVerifier(...))                          # production
    create_app(database_url, verifier=FakeVerifier(), auth_mode="dev", host="127.0.0.1")  # local

One database session per request: committed when the request succeeds,
rolled back when it raises.
"""

from flask import Flask, g, jsonify, request
from flask_cors import CORS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cloud_api.auth.decorators import require_admin, require_user
from cloud_api.auth.tokens import create_api_token, revoke_api_token
from cloud_api.auth.verify import FakeVerifier
from cloud_api import user_data
from cloud_api.dev_storage import dev_storage
from cloud_api.user_data import onboarding_state
from resume.resume_text import UnsupportedResume
from resume.storage import URL_EXPIRES_SECONDS

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def create_app(database_url: str, verifier, *, auth_mode: str = "cognito", host: str = "127.0.0.1",
               cors_origins: tuple[str, ...] = (), storage=None, cipher=None,
               admin_database_url: str | None = None) -> Flask:
    """database_url: a login in the jhi_app role (user requests).
    admin_database_url: a login in the jhi_admin_api role (admin routes);
    defaults to database_url for single-login local setups."""
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
    app.config["USER_SESSION"] = sessionmaker(bind=user_engine)     # opened by require_user
    app.config["ADMIN_SESSION"] = sessionmaker(bind=admin_engine)   # opened by require_admin
    app.config["STORAGE"] = storage     # resume.storage.S3ResumeStorage, or DevSignedStorage in dev
    app.config["CIPHER"] = cipher       # resume.store.ResumeCipher for extracted resume text
    if auth_mode == "dev":
        app.register_blueprint(dev_storage)
    if cors_origins:
        CORS(app, origins=list(cors_origins))

    @app.teardown_request
    def close_session(exc):
        db = g.pop("db", None)
        if db is None:
            return
        try:
            if exc is None:
                db.commit()
            else:
                db.rollback()
        finally:
            db.close()

    @app.get("/api/v1/me")
    @require_user
    def me():
        user = g.user
        return jsonify({
            "id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role,
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
            return jsonify(user_data.finish_resume_upload(g.db, g.user, version, app.config["STORAGE"], app.config["CIPHER"]))
        except LookupError:
            return error(404, "resume not found")
        except FileNotFoundError:
            return error(409, "the file has not been uploaded yet")
        except UnsupportedResume as exc:
            return error(415, str(exc))

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
            return jsonify(user_data.confirm_resume_skills(g.db, g.user, resume_id, skills))
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
            return jsonify(user_data.pick_level(g.db, g.user, (request.get_json(silent=True) or {}).get("level")))
        except ValueError as exc:
            return error(400, str(exc))

    @app.put("/api/v1/me/profile/scores")
    @require_user
    def confirm_scores():
        try:
            return jsonify(user_data.confirm_scores(g.db, g.user, (request.get_json(silent=True) or {}).get("scores")))
        except ValueError as exc:
            return error(400, str(exc))

    @app.get("/api/v1/jobs")
    @require_user
    def job_board():
        limit = min(max(request.args.get("limit", 100, type=int), 1), 200)
        offset = max(request.args.get("offset", 0, type=int), 0)
        return jsonify({"jobs": user_data.job_board(g.db, g.user, limit, offset), "limit": limit, "offset": offset})

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

    @app.post("/api/v1/admin/tokens")
    @require_admin
    def create_token():
        label = str((request.get_json(silent=True) or {}).get("label", "")).strip()[:100]
        if not label:
            return jsonify({"error": "label required"}), 400
        row, plaintext = create_api_token(g.db, g.user, label)
        return jsonify({"id": row.id, "label": row.label, "token": plaintext}), 201

    @app.delete("/api/v1/admin/tokens/<int:token_id>")
    @require_admin
    def revoke_token(token_id: int):
        if not revoke_api_token(g.db, g.user, token_id):
            return jsonify({"error": "no such token"}), 404
        return "", 204

    return app
