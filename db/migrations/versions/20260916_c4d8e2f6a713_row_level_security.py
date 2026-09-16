"""row-level security on per-user tables; jhi_app role for the API

The API connects as a login that is a member of jhi_app. jhi_app can read and
write the tables but does not own them, so row-level security applies to it:
a per-user row is visible or writable only when its user_id equals the
transaction's app.user_id, which cloud_api sets right after verifying who is
calling (set_config(..., true), gone at commit). Without that setting no
per-user row is visible. The table owner (migrations, batch jobs such as the
seniority backfill) is not subject to these policies.

users and api_tokens have no RLS: signing in has to find a user by the
identity provider's subject, or a token by its hash, before the caller is known.

Deployment: CREATE ROLE <api_login> LOGIN PASSWORD ...; GRANT jhi_app TO <api_login>;

Revision ID: c4d8e2f6a713
Revises: b7e3d1a5c902
Create Date: 2026-09-16
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c4d8e2f6a713"
down_revision: Union[str, Sequence[str], None] = "b7e3d1a5c902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PER_USER_TABLES = ("resumes", "user_profiles", "user_job_scores", "job_tracking", "application_events")
CURRENT_USER = "NULLIF(current_setting('app.user_id', true), '')::int"


def upgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jhi_app') THEN CREATE ROLE jhi_app NOLOGIN; END IF;
    END $$""")
    op.execute("GRANT USAGE ON SCHEMA public TO jhi_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO jhi_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO jhi_app")
    op.execute("REVOKE ALL ON alembic_version FROM jhi_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO jhi_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO jhi_app")
    for table in PER_USER_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_own_rows ON {table} "
                   f"USING (user_id = {CURRENT_USER}) WITH CHECK (user_id = {CURRENT_USER})")


def downgrade() -> None:
    for table in PER_USER_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_own_rows ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM jhi_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM jhi_app")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM jhi_app")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM jhi_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM jhi_app")
