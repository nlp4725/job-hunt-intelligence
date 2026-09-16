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
from cloud_api.user_data import onboarding_state

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def create_app(database_url: str, verifier, *, auth_mode: str = "cognito", host: str = "127.0.0.1",
               cors_origins: tuple[str, ...] = ()) -> Flask:
    if auth_mode == "dev" and host not in LOCAL_HOSTS:
        raise RuntimeError("dev login (FakeVerifier) is only allowed when the server is bound to localhost")
    if auth_mode != "dev" and isinstance(verifier, FakeVerifier):
        raise RuntimeError("FakeVerifier requires auth_mode='dev'")

    engine = create_engine(database_url, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"})
    Session = sessionmaker(bind=engine)

    app = Flask(__name__)
    app.config["VERIFIER"] = verifier
    if cors_origins:
        CORS(app, origins=list(cors_origins))

    @app.before_request
    def open_session():
        g.db = Session()

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
