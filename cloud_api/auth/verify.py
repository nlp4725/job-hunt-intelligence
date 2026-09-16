"""Who is calling: verify an identity-provider token (productization plan §5.1).

Production uses Cognito ID tokens (RS256, checked against the user pool's
published keys). Local development uses FakeVerifier, which create_app only
allows when the server is bound to localhost.
"""

import json
import re
from dataclasses import dataclass
from typing import Callable

import jwt


class InvalidToken(Exception):
    """The token is missing, malformed, forged, expired or for someone else: 401."""


@dataclass(frozen=True)
class Claims:
    sub: str              # the identity provider's user id: the only identity input
    email: str            # lowercased
    email_verified: bool


class CognitoVerifier:
    """Cognito ID tokens for one app client of one user pool.

    `jwks` is the pool's key set, or a function of the token's key id returning
    it (production: cloud_api.settings.CachedJwks, which refreshes when a new
    key id appears)."""

    def __init__(self, issuer: str, client_id: str, jwks: dict | Callable[[str | None], dict]):
        self.issuer = issuer
        self.client_id = client_id
        self._jwks = jwks

    def _key(self, kid: str | None):
        keys = self._jwks(kid) if callable(self._jwks) else self._jwks
        for key in keys.get("keys", []):
            if kid and key.get("kid") == kid:
                return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
        raise InvalidToken("unknown signing key")

    def verify(self, token: str) -> Claims:
        try:
            key = self._key(jwt.get_unverified_header(token).get("kid"))
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.client_id, issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc
        if claims.get("token_use") != "id":
            raise InvalidToken("not an ID token")
        email = (claims.get("email") or "").strip().lower()
        if not email:
            raise InvalidToken("token has no email")
        return Claims(sub=claims["sub"], email=email, email_verified=claims.get("email_verified") in (True, "true"))


class FakeVerifier:
    """Local development only: "Bearer dev:<email>" signs in as that email."""

    _PATTERN = re.compile(r"^dev:([^@\s:]+@[^@\s]+\.[^@\s]+)$")

    def verify(self, token: str) -> Claims:
        match = self._PATTERN.match(token or "")
        if not match:
            raise InvalidToken("not a dev token")
        email = match.group(1).lower()
        return Claims(sub=f"dev|{email}", email=email, email_verified=True)
