"""Admin API tokens for the extension and skill (productization plan §5.1).

A headless extension can't do an OAuth login mid-run, so it sends a long-lived
token instead. Tokens are random, shown once, stored only as a SHA-256 hash,
revocable, and valid only while their owner is an admin.
"""

import hashlib
import secrets

from db.cloud_models import ApiToken, User
from db.models import utcnow

PREFIX = "jhi_"   # tells an API token apart from an identity-provider token


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_api_token(token: str | None) -> bool:
    return bool(token) and token.startswith(PREFIX)


def create_api_token(db, user: User, label: str) -> tuple[ApiToken, str]:
    """Returns (row, plaintext). The plaintext exists only in this return value."""
    if user.role != "admin":
        raise PermissionError("only admins hold API tokens")
    plaintext = PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(user_id=user.id, token_hash=_hash(plaintext), label=label)
    db.add(row)
    db.flush()
    return row, plaintext


def authenticate_api_token(db, token: str) -> User | None:
    if not is_api_token(token):
        return None
    row = db.query(ApiToken).filter(ApiToken.token_hash == _hash(token), ApiToken.revoked_at.is_(None)).one_or_none()
    if row is None:
        return None
    user = db.get(User, row.user_id)
    if user is None or user.role != "admin":
        return None
    row.last_used_at = utcnow()
    db.flush()
    return user


def revoke_api_token(db, admin: User, token_id: int) -> bool:
    """An admin revokes one of their own tokens. False if there is no such token."""
    row = db.get(ApiToken, token_id)
    if row is None or row.user_id != admin.id:
        return False
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        db.flush()
    return True
