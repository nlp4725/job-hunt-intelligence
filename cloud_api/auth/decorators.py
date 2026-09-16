"""Route guards (productization plan §5.1).

require_user: a signed-in person (identity-provider token only; API tokens are
for the extension and skill, never for /me routes). Sets g.user.
require_admin: an admin person, or a valid admin API token. Sets g.user.
The user id always comes from the verified token, never from the request.
"""

from functools import wraps

from flask import current_app, g, jsonify, request

from cloud_api.auth.tokens import authenticate_api_token, is_api_token
from cloud_api.auth.users import AccountConflict, get_or_create_user
from cloud_api.auth.verify import InvalidToken


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
        user, error = _person(token)
        if error:
            return error
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _bearer()
        if token is None:
            return _error(401, "sign in required")
        if is_api_token(token):
            user = authenticate_api_token(g.db, token)
            if user is None:
                return _error(401, "invalid or revoked API token")
        else:
            user, error = _person(token)
            if error:
                return error
            if user.role != "admin":
                return _error(403, "admin only")
        g.user = user
        return fn(*args, **kwargs)
    return wrapper
