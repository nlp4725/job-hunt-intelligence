"""database roles for the Lambda functions

Two narrow roles, each used by one Lambda that signs in with IAM database
authentication (a short-lived token from its AWS role, no password):

- jhi_scorer (rescore Lambda): reads the shared job tables and every user's
  profile and confirmed resume skills, writes user_job_scores, drains
  rescore_queue. Row-level security would hide other users' rows, so it gets
  its own policies on exactly the tables it scores from. It cannot read
  tracking, application history, expertise data or api_tokens.
- jhi_importer (admin Lambda): seeds and reads the shared, non-private tables
  (jobs, companies, skills, collection logs, the owner's screening results).
  No per-user table, no resume text, no tokens.

Logins that use these roles are created by cloud_api/deploy_tasks.py.

Revision ID: a3d5f7b9c146
Revises: f1b3d5e7a924
Create Date: 2026-09-17
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a3d5f7b9c146"
down_revision: Union[str, Sequence[str], None] = "f1b3d5e7a924"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IMPORT_TABLES = ("companies", "jobs", "job_skills", "scrape_runs", "screening_results", "extraction_events",
                 "collection_pages")


def _create_role(name: str) -> None:
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') THEN CREATE ROLE {name} NOLOGIN; END IF;
    END $$""")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {name}")


def upgrade() -> None:
    _create_role("jhi_scorer")
    op.execute("GRANT SELECT ON jobs, job_skills, job_seniority, companies, users, user_profiles, resumes TO jhi_scorer")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_job_scores TO jhi_scorer")
    op.execute("GRANT USAGE ON SEQUENCE user_job_scores_id_seq TO jhi_scorer")
    op.execute("GRANT SELECT, UPDATE, DELETE ON rescore_queue TO jhi_scorer")   # UPDATE: FOR UPDATE SKIP LOCKED
    op.execute("CREATE POLICY scorer_reads ON user_profiles FOR SELECT TO jhi_scorer USING (true)")
    op.execute("CREATE POLICY scorer_reads ON resumes FOR SELECT TO jhi_scorer USING (true)")
    op.execute("CREATE POLICY scorer_writes ON user_job_scores FOR ALL TO jhi_scorer USING (true) WITH CHECK (true)")

    _create_role("jhi_importer")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {', '.join(IMPORT_TABLES)} TO jhi_importer")
    op.execute(f"GRANT USAGE, SELECT, UPDATE ON SEQUENCE {', '.join(t + '_id_seq' for t in IMPORT_TABLES)} TO jhi_importer")
    op.execute("GRANT SELECT ON job_seniority, rescore_queue TO jhi_importer")


def downgrade() -> None:
    for policy, table in (("scorer_reads", "user_profiles"), ("scorer_reads", "resumes"), ("scorer_writes", "user_job_scores")):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    for role in ("jhi_scorer", "jhi_importer"):
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE USAGE ON SCHEMA public FROM {role}")
