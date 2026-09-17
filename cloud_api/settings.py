"""Production settings, read from environment variables. In AWS, ECS injects
them: plain values from the task definition, passwords and keys from Secrets
Manager (terraform/app/ecs.tf). Nothing here has a default that could silently point
production at the wrong place: a missing variable stops the process.
"""

import base64
import hashlib
import json
import os
import threading
import time
import urllib.request
from urllib.parse import quote

from sqlalchemy.engine import URL


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"environment variable {name} is required")
    return value


def database_url(user_var: str, password_var: str) -> str:
    """A psycopg URL for one login. TLS is required (RDS); DB_SSLMODE exists only
    so tests can reach a local Postgres without TLS."""
    return URL.create(
        "postgresql+psycopg", username=required(user_var), password=required(password_var),
        host=required("DB_HOST"), port=int(os.environ.get("DB_PORT", "5432")), database=required("DB_NAME"),
        query={"sslmode": os.environ.get("DB_SSLMODE", "require")},
    ).render_as_string(hide_password=False)


class CachedJwks:
    """The Cognito user pool's public keys, fetched on first use and refreshed
    hourly, or at most once a minute when a token names a key not seen yet
    (key rotation)."""

    MAX_AGE, RETRY_AFTER = 3600, 60

    def __init__(self, url: str, fetch=None):
        self.url = url
        self._fetch = fetch or self._http_get
        self._keys, self._fetched_at = None, 0.0
        self._lock = threading.Lock()

    def _http_get(self) -> dict:
        with urllib.request.urlopen(self.url, timeout=5) as response:   # noqa: S310  fixed https URL
            return json.load(response)

    def __call__(self, kid: str | None = None) -> dict:
        with self._lock:
            age = time.monotonic() - self._fetched_at
            known = self._keys is not None and any(k.get("kid") == kid for k in self._keys.get("keys", []))
            if self._keys is None or age > self.MAX_AGE or (kid and not known and age > self.RETRY_AFTER):
                self._keys, self._fetched_at = self._fetch(), time.monotonic()
            return self._keys


def cognito_issuer(region: str, pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{quote(pool_id)}"


def owner_database_url() -> str:
    """Batch workers and the deploy task: JHI_DATABASE_URL when set (local),
    otherwise the RDS master login from DB_OWNER_USER / DB_OWNER_PASSWORD."""
    return os.environ.get("JHI_DATABASE_URL") or database_url("DB_OWNER_USER", "DB_OWNER_PASSWORD")


def fernet_key_from_secret(secret: str) -> bytes:
    """JHI_RESUME_KEY as a Fernet key. Secrets Manager generates a random string,
    not Fernet's base64 format, so a non-Fernet value is hashed into a 32-byte
    key (SHA-256 of a 64-character random string keeps the full 256 bits). A
    valid Fernet key (local use) is used as is."""
    raw = secret.strip().encode()
    try:
        if len(base64.urlsafe_b64decode(raw)) == 32 and len(raw) == 44:
            return raw
    except ValueError:
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
