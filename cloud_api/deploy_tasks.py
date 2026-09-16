"""One-off deploy step, run as a Fargate task before new API tasks start:

    python -m cloud_api.deploy_tasks migrate

Connects as the RDS master user (the table owner), runs Alembic to head, then
creates or updates the two API logins with the passwords in Secrets Manager
and grants each its role. Safe to run on every deploy.

Environment: DB_HOST, DB_PORT, DB_NAME, DB_OWNER_USER, DB_OWNER_PASSWORD,
JHI_APP_DB_USER, JHI_APP_DB_PASSWORD, JHI_ADMIN_DB_USER, JHI_ADMIN_DB_PASSWORD.
"""

import sys
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from cloud_api.settings import owner_database_url, required

REPO = Path(__file__).resolve().parent.parent
LOGINS = (("JHI_APP_DB_USER", "JHI_APP_DB_PASSWORD", "jhi_app"),
          ("JHI_ADMIN_DB_USER", "JHI_ADMIN_DB_PASSWORD", "jhi_admin_api"))


def upgrade(owner_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", owner_url.replace("%", "%%"))
    command.upgrade(config, "head")


def ensure_logins(owner_url: str) -> None:
    url = make_url(owner_url)
    conninfo = dict(host=url.host, port=url.port, dbname=url.database, user=url.username,
                    password=url.password, sslmode=url.query.get("sslmode", "require"), connect_timeout=10)
    with psycopg.connect(**conninfo, autocommit=True) as conn:
        for user_var, password_var, role in LOGINS:
            login, password = required(user_var), required(password_var)
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(login), sql.Literal(password)))
            conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(role), sql.Identifier(login)))


def main(argv: list[str]) -> None:
    if argv != ["migrate"]:
        sys.exit("usage: python -m cloud_api.deploy_tasks migrate")
    owner_url = owner_database_url()
    upgrade(owner_url)
    ensure_logins(owner_url)
    print("migrated and logins ensured", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
