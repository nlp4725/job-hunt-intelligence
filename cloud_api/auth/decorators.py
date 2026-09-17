"""Route guards (productization plan §5.1).

require_user: a signed-in person (identity-provider token only; API tokens are
for the extension and skill, never for /me routes). Sets g.user.
Admin routes accept an admin person, or an API token whose scope the route allows:
    require_collector     scope collector or admin (the extension's routes)
    require_admin         scope admin
    require_admin_person  no API tokens at all (creating tokens, so a leaked token can't mint more)
The user id always comes from the verified token, never from the request, and
is handed to Postgres row-level security for the request's transaction.
"""

from functools import wraps

from flask import current_app, g, jsonify, request

from cloud_api.auth.tokens import authenticate_api_token_scoped, is_api_token
from cloud_api.auth.users import AccountConflict, get_or_create_user
from cloud_api.auth.verify import InvalidToken
from cloud_api.observability import log_event, metric
from cloud_api.user_data import set_request_user


def _error(status: int, message: str):
    return jsonify({"error": message}), status


def _bearer() -> str | None:
    header = request.headers.get("Authorization", "")
    return (header[len("Bearer "):].strip() or None) if header.startswith("Bearer ") else None


def _person(token: str):
    """(user, error response) for an identity-provider token."""
    try:
        claims = current_app.config["VERIFIER"].verify(token)
    except InvalidToken:
        return None, _error(401, "invalid or expired sign-in")
    try:
        return get_or_create_user(g.db, claims), None
    except AccountConflict as exc:
        return None, _error(403, str(exc))


def require_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _bearer()
        if token is None or is_api_token(token):
            return _error(401, "sign in required")
        g.db = current_app.config["USER_SESSION"]()    # database role for user requests
        user, error = _person(token)
        if error:
            return error
        g.user = user
        set_request_user(g.db, user.id)   # row-level security for the rest of this request
        return fn(*args, **kwargs)
    return wrapper


def _admin_guard(token_scopes: frozenset[str]):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = _bearer()
            if token is None:
                return _error(401, "sign in required")
            g.db = current_app.config["ADMIN_SESSION"]()   # database role for admin routes
            if is_api_token(token):
                found = authenticate_api_token_scoped(g.db, token)
                if found is None:
                    return _error(401, "invalid or revoked API token")
                user, scope = found
                if scope not in token_scopes:
                    metric("TokenScopeDenied", dimensions={"Scope": scope})
                    log_event("token_scope_denied", level="warning", scope=scope, route=request.url_rule.rule if request.url_rule else None)
                    return _error(403, "this token's scope does not allow this route")
                g.token_scope = scope
            else:
                user, error = _person(token)
                if error:
                    return error
                if user.role != "admin":
                    return _error(403, "admin only")
            g.user = user
            set_request_user(g.db, user.id)   # admins see only their own per-user rows too
            return fn(*args, **kwargs)
        return wrapper
    return decorator


require_collector = _admin_guard(frozenset({"collector", "admin"}))
require_admin = _admin_guard(frozenset({"admin"}))
require_admin_person = _admin_guard(frozenset())
