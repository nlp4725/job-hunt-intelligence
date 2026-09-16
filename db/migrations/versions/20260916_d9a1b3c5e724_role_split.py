"""separate database roles for user requests and admin routes

jhi_app (user requests) keeps only what a signed-in user needs:
- SELECT on the shared board tables (jobs, companies, job_skills, job_seniority);
- SELECT, INSERT, UPDATE, DELETE on the per-user tables (row-level security
  already limits these to the caller's rows);
- users: SELECT all (sign-in looks users up before the caller is known),
  INSERT only plain users, UPDATE and DELETE only its own row, and never the
  role column.
Nothing else: no writes to shared tables, no api_tokens, none of the owner's
local-only tables (screening_results, collection logs...).

jhi_admin_api (admin routes: captures, tokens, expiry) reads and writes the
shared tables and api_tokens, and has no access to any per-user table.

Default privileges are removed: every future table is granted explicitly in
its own migration.

Deployment: GRANT jhi_app TO <user_api_login>; GRANT jhi_admin_api TO <admin_api_login>;

Revision ID: d9a1b3c5e724
Revises: c4d8e2f6a713
Create Date: 2026-09-16
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d9a1b3c5e724"
down_revision: Union[str, Sequence[str], None] = "c4d8e2f6a713"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BOARD = ("jobs", "companies", "job_skills", "job_seniority")
OWNER_SHARED = ("screening_results", "resume", "career_goals", "chat_messages",
                "collection_pages", "extraction_events", "scrape_runs")
PER_USER = ("resumes", "user_profiles", "user_job_scores", "job_tracking", "application_events")
CURRENT_USER = "NULLIF(current_setting('app.user_id', true), '')::int"


def _sequences(tables) -> str:
    return ", ".join(f"{t}_id_seq" for t in tables)


def upgrade() -> None:
    # jhi_app: start from nothing, then grant exactly what user requests need
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM jhi_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM jhi_app")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM jhi_app")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM jhi_app")
    op.execute(f"GRANT SELECT ON {', '.join(BOARD)} TO jhi_app")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(PER_USER)} TO jhi_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON users TO jhi_app")
    op.execute("GRANT UPDATE (idp_subject, email, display_name, last_active_at, taxonomy_version_seen, deleted_at) ON users TO jhi_app")
    op.execute(f"GRANT USAGE ON SEQUENCE users_id_seq, {_sequences(PER_USER)} TO jhi_app")

    # users: anyone may look up a user; creating is limited to plain users; changes only to one's own row
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY users_select ON users FOR SELECT USING (true)")
    op.execute("CREATE POLICY users_insert ON users FOR INSERT WITH CHECK (role = 'user')")
    op.execute(f"CREATE POLICY users_update_own ON users FOR UPDATE USING (id = {CURRENT_USER}) WITH CHECK (id = {CURRENT_USER})")
    op.execute(f"CREATE POLICY users_delete_own ON users FOR DELETE USING (id = {CURRENT_USER})")

    # jhi_admin_api: shared tables and tokens, never per-user tables
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jhi_admin_api') THEN CREATE ROLE jhi_admin_api NOLOGIN; END IF;
    END $$""")
    op.execute("GRANT USAGE ON SCHEMA public TO jhi_admin_api")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(BOARD + OWNER_SHARED)}, api_tokens TO jhi_admin_api")
    op.execute("GRANT SELECT, INSERT ON users TO jhi_admin_api")
    op.execute("GRANT UPDATE (idp_subject, email, display_name, last_active_at) ON users TO jhi_admin_api")
    op.execute(f"GRANT USAGE ON SEQUENCE users_id_seq, api_tokens_id_seq, {_sequences(BOARD + OWNER_SHARED)} TO jhi_admin_api")


def downgrade() -> None:
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM jhi_admin_api")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM jhi_admin_api")
    op.execute("REVOKE USAGE ON SCHEMA public FROM jhi_admin_api")
    for policy in ("users_select", "users_insert", "users_update_own", "users_delete_own"):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON users")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM jhi_app")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM jhi_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO jhi_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO jhi_app")
    op.execute("REVOKE ALL ON alembic_version FROM jhi_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO jhi_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO jhi_app")
