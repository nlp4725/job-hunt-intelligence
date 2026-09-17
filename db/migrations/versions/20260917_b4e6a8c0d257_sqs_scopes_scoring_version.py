"""rescoring moves to SQS; scoped API tokens; scoring logic version

- rescore_queue is dropped. The API now publishes "score this job" and
  "rescore this user" messages to SQS after its transaction commits, and the
  jhi-rescore Lambda consumes them. An hourly reconciliation (same Lambda)
  finds anything a lost message left unscored.
- user_job_scores.scoring_version: which scoring logic produced the row, so a
  logic change is picked up by reconciliation.
- api_tokens.scope: "collector" (the extension's captures) or "admin".
  Existing tokens become collector tokens.

Revision ID: b4e6a8c0d257
Revises: a3d5f7b9c146
Create Date: 2026-09-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4e6a8c0d257"
down_revision: Union[str, Sequence[str], None] = "a3d5f7b9c146"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("rescore_queue")
    op.add_column("user_job_scores", sa.Column("scoring_version", sa.String(), nullable=True))
    op.add_column("api_tokens", sa.Column("scope", sa.String(), server_default="collector", nullable=False))
    op.create_check_constraint("ck_api_tokens_scope", "api_tokens", "scope IN ('collector', 'admin')")


def downgrade() -> None:
    op.drop_constraint("ck_api_tokens_scope", "api_tokens", type_="check")
    op.drop_column("api_tokens", "scope")
    op.drop_column("user_job_scores", "scoring_version")
    op.create_table(
        "rescore_queue",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.execute("GRANT SELECT, INSERT ON rescore_queue TO jhi_admin_api")
    op.execute("GRANT SELECT, UPDATE, DELETE ON rescore_queue TO jhi_scorer")
    op.execute("GRANT SELECT ON rescore_queue TO jhi_importer")
