"""job_seniority: drop is_agency

Decided 2026-09-16: agency postings are filtered out before screening
(judge/agency_blocklist.py), so seniority classification no longer judges them.

Revision ID: b7e3d1a5c902
Revises: 9a4f2c6e8b11
Create Date: 2026-09-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e3d1a5c902"
down_revision: Union[str, Sequence[str], None] = "9a4f2c6e8b11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("job_seniority", "is_agency")


def downgrade() -> None:
    op.add_column("job_seniority", sa.Column("is_agency", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("job_seniority", "is_agency", server_default=None)
