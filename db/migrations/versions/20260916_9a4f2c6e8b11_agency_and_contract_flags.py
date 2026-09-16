"""job_seniority: is_agency and is_contract flags replace non_fit_reason

Decided 2026-09-16: agency and contract are independent yes/no attributes, so a
staffing firm's fixed-term placement can be both. Existing rows are backfilled
from non_fit_reason.

Revision ID: 9a4f2c6e8b11
Revises: 5c1e7a9b2d40
Create Date: 2026-09-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9a4f2c6e8b11"
down_revision: Union[str, Sequence[str], None] = "5c1e7a9b2d40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job_seniority", sa.Column("is_agency", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("job_seniority", sa.Column("is_contract", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE job_seniority SET is_agency = (non_fit_reason = 'agency'), is_contract = (non_fit_reason = 'contract') "
               "WHERE non_fit_reason IS NOT NULL")
    op.drop_constraint("ck_job_seniority_non_fit_reason", "job_seniority", type_="check")
    op.drop_column("job_seniority", "non_fit_reason")
    op.alter_column("job_seniority", "is_agency", server_default=None)
    op.alter_column("job_seniority", "is_contract", server_default=None)


def downgrade() -> None:
    op.add_column("job_seniority", sa.Column("non_fit_reason", sa.String(), nullable=True))
    op.execute("UPDATE job_seniority SET non_fit_reason = CASE WHEN is_agency THEN 'agency' "
               "WHEN is_contract THEN 'contract' END")
    op.create_check_constraint("ck_job_seniority_non_fit_reason", "job_seniority", "non_fit_reason IN ('agency', 'contract')")
    op.drop_column("job_seniority", "is_contract")
    op.drop_column("job_seniority", "is_agency")
