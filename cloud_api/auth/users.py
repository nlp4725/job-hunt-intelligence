"""Turn verified claims into a user row (productization plan §5.1).

Users are keyed on the identity provider's `sub`, never on email. The single
exception: a row created before its owner ever logged in (the seeded owner,
idp_subject still NULL) can be claimed by a login with the same email, and
only when the provider says that email is verified.
"""

from sqlalchemy import func

from cloud_api.auth.verify import Claims
from db.cloud_models import User
from db.models import utcnow


class AccountConflict(Exception):
    """The email belongs to another account, or an unverified email tried to claim a row: 403."""


def get_or_create_user(db, claims: Claims) -> User:
    user = db.query(User).filter(User.idp_subject == claims.sub).one_or_none()
    if user is None:
        existing = db.query(User).filter(func.lower(User.email) == claims.email.lower()).one_or_none()
        if existing is None:
            user = User(idp_subject=claims.sub, email=claims.email.lower(), role="user")
            db.add(user)
        elif existing.idp_subject is None and claims.email_verified:
            existing.idp_subject = claims.sub
            user = existing
        else:
            raise AccountConflict("this email belongs to another account")
    user.last_active_at = utcnow()
    db.flush()
    return user
