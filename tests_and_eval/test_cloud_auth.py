"""Phase 5, slice 1: authentication for the cloud API (productization plan §5.1).

- Browser users sign in with Cognito; the API verifies the ID token itself.
- Local development uses FakeVerifier ("Bearer dev:<email>"), allowed only
  when the app is bound to localhost.
- Users are keyed on the token's `sub`, never on email. The one exception is
  claiming a row created before its owner ever logged in (the seeded owner):
  a verified email may claim a row whose idp_subject is still empty.
- The extension and skill use admin API tokens: random, stored hashed, shown
  once, revocable, admin-only.

Postgres tests need JHI_TEST_POSTGRES_URL (see test_cloud_db.py).
"""

import hashlib
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.orm import sessionmaker

from tests_and_eval.test_cloud_db import PG_URL, _upgrade, needs_pg, pg_engine  # noqa: F401  (fixture)

ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool"
CLIENT_ID = "test-client-id"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks(signing_key):
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    return {"keys": [{**public, "kid": "key-1", "alg": "RS256", "use": "sig"}]}


def _id_token(signing_key, **overrides) -> str:
    now = int(time.time())
    claims = {"sub": "cognito-sub-1", "email": "a@example.com", "email_verified": True, "iss": ISSUER,
              "aud": CLIENT_ID, "token_use": "id", "iat": now, "exp": now + 3600, **overrides}
    headers = {"kid": overrides.pop("_kid", "key-1")} if "_kid" not in claims else {"kid": claims.pop("_kid")}
    return jwt.encode(claims, signing_key, algorithm="RS256", headers=headers)


class TestCognitoVerifier:
    def _verifier(self, jwks):
        from cloud_api.auth.verify import CognitoVerifier

        return CognitoVerifier(issuer=ISSUER, client_id=CLIENT_ID, jwks=jwks)

    def test_a_valid_id_token_gives_its_claims(self, signing_key, jwks):
        claims = self._verifier(jwks).verify(_id_token(signing_key))
        assert (claims.sub, claims.email, claims.email_verified) == ("cognito-sub-1", "a@example.com", True)

    @pytest.mark.parametrize("overrides", [
        {"exp": int(time.time()) - 60},                     # expired
        {"iss": "https://cognito-idp.us-east-1.amazonaws.com/other-pool"},
        {"aud": "someone-elses-client"},
        {"token_use": "access"},                            # an access token is not an ID token
        {"_kid": "unknown-key"},
    ], ids=["expired", "wrong-issuer", "wrong-audience", "access-token", "unknown-key"])
    def test_invalid_tokens_are_rejected(self, signing_key, jwks, overrides):
        from cloud_api.auth.verify import InvalidToken

        with pytest.raises(InvalidToken):
            self._verifier(jwks).verify(_id_token(signing_key, **overrides))

    def test_a_token_signed_with_a_shared_secret_is_rejected(self, jwks):
        from cloud_api.auth.verify import InvalidToken

        forged = jwt.encode({"sub": "x", "iss": ISSUER, "aud": CLIENT_ID, "token_use": "id",
                             "exp": int(time.time()) + 60}, "guessable-secret", algorithm="HS256", headers={"kid": "key-1"})
        with pytest.raises(InvalidToken):
            self._verifier(jwks).verify(forged)

    def test_garbage_is_rejected(self, jwks):
        from cloud_api.auth.verify import InvalidToken

        with pytest.raises(InvalidToken):
            self._verifier(jwks).verify("not.a.jwt")


class TestFakeVerifier:
    def test_dev_tokens_name_an_email(self):
        from cloud_api.auth.verify import FakeVerifier

        claims = FakeVerifier().verify("dev:b@example.com")
        assert (claims.sub, claims.email, claims.email_verified) == ("dev|b@example.com", "b@example.com", True)

    @pytest.mark.parametrize("token", ["b@example.com", "dev:", "dev:not-an-email"])
    def test_anything_else_is_rejected(self, token):
        from cloud_api.auth.verify import FakeVerifier, InvalidToken

        with pytest.raises(InvalidToken):
            FakeVerifier().verify(token)


@pytest.fixture
def db(pg_engine):  # noqa: F811
    _upgrade(PG_URL)
    session = sessionmaker(bind=pg_engine)()
    yield session
    session.rollback()
    session.close()


def _claims(sub="sub-a", email="a@example.com", verified=True):
    from cloud_api.auth.verify import Claims

    return Claims(sub=sub, email=email, email_verified=verified)


@needs_pg
class TestGetOrCreateUser:
    def test_first_login_creates_a_regular_user(self, db):
        from cloud_api.auth.users import get_or_create_user

        user = get_or_create_user(db, _claims())
        assert (user.idp_subject, user.email, user.role) == ("sub-a", "a@example.com", "user")
        assert user.last_active_at is not None

    def test_the_same_subject_is_the_same_user_even_if_the_email_changed(self, db):
        from cloud_api.auth.users import get_or_create_user

        first = get_or_create_user(db, _claims())
        again = get_or_create_user(db, _claims(email="new-address@example.com"))
        assert again.id == first.id

    def test_a_verified_login_claims_the_seeded_owner_row(self, db):
        from cloud_api.auth.users import get_or_create_user
        from db.cloud_models import User

        db.add(User(id=1, email="owner@example.com", role="admin"))
        db.flush()
        user = get_or_create_user(db, _claims(sub="owner-sub", email="Owner@Example.com"))
        assert (user.id, user.role, user.idp_subject) == (1, "admin", "owner-sub")

    def test_an_unverified_email_cannot_claim_a_row(self, db):
        from cloud_api.auth.users import AccountConflict, get_or_create_user
        from db.cloud_models import User

        db.add(User(id=1, email="owner@example.com", role="admin"))
        db.flush()
        with pytest.raises(AccountConflict):
            get_or_create_user(db, _claims(sub="attacker", email="owner@example.com", verified=False))
        assert db.get(User, 1).idp_subject is None

    def test_an_email_already_owned_by_another_login_is_refused(self, db):
        from cloud_api.auth.users import AccountConflict, get_or_create_user

        get_or_create_user(db, _claims(sub="sub-a", email="a@example.com"))
        with pytest.raises(AccountConflict):
            get_or_create_user(db, _claims(sub="sub-b", email="a@example.com"))


@needs_pg
class TestApiTokens:
    def _admin(self, db):
        from db.cloud_models import User

        admin = User(email="owner@example.com", role="admin", idp_subject="owner-sub")
        db.add(admin)
        db.flush()
        return admin

    def test_a_token_is_shown_once_and_stored_only_as_a_hash(self, db):
        from cloud_api.auth.tokens import authenticate_api_token, create_api_token

        admin = self._admin(db)
        row, plaintext = create_api_token(db, admin, "extension-macbook")
        assert plaintext.startswith("jhi_") and row.token_hash == hashlib.sha256(plaintext.encode()).hexdigest()
        assert plaintext not in json.dumps({c.name: str(getattr(row, c.name)) for c in row.__table__.columns})
        assert authenticate_api_token(db, plaintext).id == admin.id
        assert row.last_used_at is not None

    def test_a_revoked_or_unknown_token_does_not_authenticate(self, db):
        from cloud_api.auth.tokens import authenticate_api_token, create_api_token, revoke_api_token

        admin = self._admin(db)
        row, plaintext = create_api_token(db, admin, "old laptop")
        revoke_api_token(db, admin, row.id)
        assert authenticate_api_token(db, plaintext) is None
        assert authenticate_api_token(db, "jhi_" + "x" * 43) is None

    def test_only_admins_hold_tokens(self, db):
        from cloud_api.auth.tokens import authenticate_api_token, create_api_token
        from cloud_api.auth.users import get_or_create_user

        user = get_or_create_user(db, _claims())
        with pytest.raises(PermissionError):
            create_api_token(db, user, "nope")
        admin = self._admin(db)
        _, plaintext = create_api_token(db, admin, "extension")
        admin.role = "user"                     # demoted later
        db.flush()
        assert authenticate_api_token(db, plaintext) is None


@pytest.fixture
def client(pg_engine):  # noqa: F811
    from cloud_api.app import create_app
    from cloud_api.auth.verify import FakeVerifier

    _upgrade(PG_URL)
    app = create_app(PG_URL, verifier=FakeVerifier(), auth_mode="dev", host="127.0.0.1")
    app.config["TESTING"] = True
    return app.test_client()


def _seed_owner(pg_engine):  # noqa: F811
    from db.cloud_models import User

    with sessionmaker(bind=pg_engine)() as session:
        session.add(User(email="owner@example.com", role="admin"))   # not yet claimed by a login
        session.commit()


@needs_pg
class TestApi:
    def test_me_needs_a_token(self, client):
        assert client.get("/api/v1/me").status_code == 401
        assert client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-dev-token"}).status_code == 401

    def test_me_creates_the_user_on_first_login(self, client):
        response = client.get("/api/v1/me", headers={"Authorization": "Bearer dev:a@example.com"})
        assert response.status_code == 200
        body = response.get_json()
        assert (body["email"], body["role"]) == ("a@example.com", "user")
        assert body["onboarding"] == {"resume": False, "skills_confirmed": False, "level": False, "scores_confirmed": False,
                                      "complete": False, "expertise": "locked"}

    def test_admin_routes_refuse_users_and_accept_the_owner(self, client, pg_engine):  # noqa: F811
        _seed_owner(pg_engine)
        user, owner = {"Authorization": "Bearer dev:a@example.com"}, {"Authorization": "Bearer dev:owner@example.com"}
        assert client.post("/api/v1/admin/tokens", json={"label": "x"}, headers=user).status_code == 403
        created = client.post("/api/v1/admin/tokens", json={"label": "extension"}, headers=owner)
        assert created.status_code == 201 and created.get_json()["token"].startswith("jhi_")

    def test_an_admin_api_token_works_until_revoked(self, client, pg_engine):  # noqa: F811
        _seed_owner(pg_engine)
        owner = {"Authorization": "Bearer dev:owner@example.com"}
        created = client.post("/api/v1/admin/tokens", json={"label": "extension"}, headers=owner).get_json()
        with_token = {"Authorization": f"Bearer {created['token']}"}
        assert client.post("/api/v1/admin/tokens", json={"label": "second"}, headers=with_token).status_code == 201
        assert client.delete(f"/api/v1/admin/tokens/{created['id']}", headers=owner).status_code == 204
        assert client.post("/api/v1/admin/tokens", json={"label": "third"}, headers=with_token).status_code == 401

    def test_an_api_token_is_not_a_user_login(self, client, pg_engine):  # noqa: F811
        """Admin tokens are for the extension and skill, never for /me routes."""
        _seed_owner(pg_engine)
        token = client.post("/api/v1/admin/tokens", json={"label": "extension"},
                            headers={"Authorization": "Bearer dev:owner@example.com"}).get_json()["token"]
        assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    def test_an_email_conflict_is_a_403_not_a_crash(self, client, pg_engine):  # noqa: F811
        from db.cloud_models import User

        with sessionmaker(bind=pg_engine)() as session:
            session.add(User(email="taken@example.com", idp_subject="someone-else"))
            session.commit()
        assert client.get("/api/v1/me", headers={"Authorization": "Bearer dev:taken@example.com"}).status_code == 403


def test_dev_login_is_refused_unless_bound_to_localhost():
    from cloud_api.app import create_app
    from cloud_api.auth.verify import FakeVerifier

    with pytest.raises(RuntimeError, match="localhost"):
        create_app("postgresql+psycopg://unused/unused", verifier=FakeVerifier(), auth_mode="dev", host="0.0.0.0")
