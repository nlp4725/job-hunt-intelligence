"""the admin Lambda may write seniority levels

import_levels (cloud_api/lambda_handlers.py) carries levels classified on
another machine into job_seniority; jhi_importer could only read that table.

Revision ID: c5f7a9b1d368
Revises: b4e6a8c0d257
Create Date: 2026-09-17
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c5f7a9b1d368"
down_revision: Union[str, Sequence[str], None] = "b4e6a8c0d257"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT INSERT, UPDATE ON job_seniority TO jhi_importer")
    op.execute("GRANT USAGE, SELECT, UPDATE ON SEQUENCE job_seniority_id_seq TO jhi_importer")


def downgrade() -> None:
    op.execute("REVOKE INSERT, UPDATE ON job_seniority FROM jhi_importer")
    op.execute("REVOKE ALL ON SEQUENCE job_seniority_id_seq FROM jhi_importer")
