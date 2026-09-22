"""the scorer may read the owner's local screening results

adopt_local_scores (db/adopt_local_scores.py) turns the single-user scores the
seed carried up into per-user rows for the owner, so a board full of already
paid-for screening does not have to be recomputed. Only the scorer login does
that work, and it could not read the table.

Revision ID: e7b9c1d3f580
Revises: d6a8b0c2e479
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e7b9c1d3f580"
down_revision: Union[str, Sequence[str], None] = "d6a8b0c2e479"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON screening_results TO jhi_scorer")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON screening_results FROM jhi_scorer")
