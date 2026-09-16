"""paid tier: plan on users, expertise profiles and per-user expertise scores

Expertise Match is a paid-member feature and an optional onboarding step:
- users.plan ('free' | 'paid'). jhi_app cannot set it (no column grant, and a
  new user row must start free). Until payments exist, jhi_admin_api sets it
  only through set_user_plan(email, plan), a SECURITY DEFINER function that
  changes that one column, rather than an update grant on other users' rows.
- users.expertise_skipped_at: the user skipped the step (free or paid).
- expertise_profiles: per-user, RLS, read and written by jhi_app.
- user_job_expertise: per-user, RLS, jhi_app may only read; the owner-run
  expertise worker writes it (one LLM call per user and job).

Revision ID: f1b3d5e7a924
Revises: e2f4a6c8d035
Create Date: 2026-09-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1b3d5e7a924"
down_revision: Union[str, Sequence[str], None] = "e2f4a6c8d035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CURRENT_USER = "NULLIF(current_setting('app.user_id', true), '')::int"


def upgrade() -> None:
    op.add_column("users", sa.Column("plan", sa.String(), server_default="free", nullable=False))
    op.add_column("users", sa.Column("expertise_skipped_at", sa.DateTime(), nullable=True))
    op.create_check_constraint("ck_users_plan", "users", "plan IN ('free', 'paid')")

    op.create_table(
        "expertise_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("resume_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("main_work", sa.JSON(), nullable=False),
        sa.Column("dream", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "version", name="uq_expertise_profiles_user_version"),
    )
    op.create_table(
        "user_job_expertise",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("domain_score", sa.Integer(), nullable=False),
        sa.Column("capability_score", sa.Integer(), nullable=False),
        sa.Column("dream_score", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("expertise_score", sa.Float(), nullable=False),
        sa.Column("scored_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "job_id", name="uq_user_job_expertise_user_job"),
    )
    for table in ("expertise_profiles", "user_job_expertise"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_own_rows ON {table} "
                   f"USING (user_id = {CURRENT_USER}) WITH CHECK (user_id = {CURRENT_USER})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON expertise_profiles TO jhi_app")
    op.execute("GRANT USAGE ON SEQUENCE expertise_profiles_id_seq TO jhi_app")
    op.execute("GRANT SELECT ON user_job_expertise TO jhi_app")
    op.execute("GRANT UPDATE (expertise_skipped_at) ON users TO jhi_app")

    # A user signing up can only create a free account.
    op.execute("DROP POLICY users_insert ON users")
    op.execute("CREATE POLICY users_insert ON users FOR INSERT WITH CHECK (role = 'user' AND plan = 'free')")
    op.execute("""
        CREATE FUNCTION set_user_plan(target_email text, new_plan text) RETURNS integer
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
            WITH changed AS (
                UPDATE users SET plan = new_plan
                WHERE lower(email) = lower(target_email) AND deleted_at IS NULL
                RETURNING id)
            SELECT id FROM changed
        $$""")
    op.execute("REVOKE ALL ON FUNCTION set_user_plan(text, text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION set_user_plan(text, text) TO jhi_admin_api")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS set_user_plan(text, text)")
    op.execute("DROP POLICY users_insert ON users")
    op.execute("CREATE POLICY users_insert ON users FOR INSERT WITH CHECK (role = 'user')")
    op.execute("REVOKE UPDATE (expertise_skipped_at) ON users FROM jhi_app")
    op.drop_table("user_job_expertise")
    op.drop_table("expertise_profiles")
    op.drop_constraint("ck_users_plan", "users", type_="check")
    op.drop_column("users", "expertise_skipped_at")
    op.drop_column("users", "plan")
