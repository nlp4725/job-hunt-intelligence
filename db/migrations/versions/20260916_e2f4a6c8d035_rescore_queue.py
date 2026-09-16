"""rescore_queue: jobs captured since users were last scored

The capture route runs as jhi_admin_api, which cannot touch per-user tables,
so it only queues the job; cloud_api/rescore_worker.py runs as the table owner
and scores queued jobs for every user.

Revision ID: e2f4a6c8d035
Revises: d9a1b3c5e724
Create Date: 2026-09-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f4a6c8d035"
down_revision: Union[str, Sequence[str], None] = "d9a1b3c5e724"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rescore_queue",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.execute("GRANT SELECT, INSERT ON rescore_queue TO jhi_admin_api")


def downgrade() -> None:
    op.drop_table("rescore_queue")
