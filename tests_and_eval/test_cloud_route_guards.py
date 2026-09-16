"""Phase 5, slice 3C: route-level guards over every registered route
(productization plan §5.4). Walking app.url_map means a route added later is
covered without anyone remembering to add a test for it.

Postgres tests need JHI_TEST_POSTGRES_URL.
"""

import ast
import re
from pathlib import Path

import pytest

from tests_and_eval.test_cloud_db import PG_URL, _upgrade, needs_pg, pg_engine  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parent.parent
PUBLIC_PREFIXES = ("/static", "/dev-storage", "/healthz")   # dev-storage exists only in dev mode and checks its own signatures; healthz returns {"ok": true} only
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


@pytest.fixture
def app(pg_engine):  # noqa: F811
    from cloud_api.app import create_app
    from cloud_api.auth.verify import FakeVerifier

    _upgrade(PG_URL)
    app = create_app(PG_URL, verifier=FakeVerifier(), auth_mode="dev", host="127.0.0.1")
    app.config["TESTING"] = True
    return app


def _routes(app):
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith(PUBLIC_PREFIXES):
            continue
        path = re.sub(r"<(?:\w+:)?\w+>", "1", rule.rule)
        for method in sorted(rule.methods & set(METHODS)):
            yield method, path, rule


@needs_pg
def test_every_route_requires_sign_in(app):
    client = app.test_client()
    open_routes = [f"{m} {p}" for m, p, _ in _routes(app) if client.open(p, method=m).status_code != 401]
    assert open_routes == []


@needs_pg
def test_every_admin_route_rejects_a_signed_in_user(app):
    client = app.test_client()
    user = {"Authorization": "Bearer dev:someone@example.com"}
    admin_routes = [(m, p) for m, p, rule in _routes(app) if rule.rule.startswith("/api/v1/admin")]
    assert admin_routes, "expected admin routes"
    allowed = [f"{m} {p}" for m, p in admin_routes if client.open(p, method=m, headers=user).status_code != 403]
    assert allowed == []


@needs_pg
def test_no_route_takes_a_user_id(app):
    params = {arg for rule in app.url_map.iter_rules() for arg in rule.arguments}
    assert not [p for p in params if "user" in p]


def test_no_cloud_api_code_reads_a_user_id_from_the_request():
    """The caller's id comes only from the verified token (g.user)."""
    offenders = []
    for path in sorted((REPO / "cloud_api").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "__getitem__"):
                source = ast.unparse(node)
                if "request" in source and "user" in source.lower():
                    offenders.append(f"{path.relative_to(REPO)}: {source}")
            if isinstance(node, ast.Subscript) and "request" in ast.unparse(node.value) and "user" in ast.unparse(node.slice).lower():
                offenders.append(f"{path.relative_to(REPO)}: {ast.unparse(node)}")
    assert offenders == []


def test_dev_storage_routes_do_not_exist_outside_dev_mode():
    from cloud_api.app import create_app
    from cloud_api.auth.verify import CognitoVerifier

    app = create_app("postgresql+psycopg://unused@localhost/unused",
                     verifier=CognitoVerifier(issuer="https://example", client_id="x", jwks={"keys": []}))
    assert not [r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/dev-storage")]
