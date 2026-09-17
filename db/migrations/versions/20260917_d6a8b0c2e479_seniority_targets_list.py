"""a user picks up to three seniority levels, not one

Decided 2026-09-17: onboarding lets someone pick up to three levels and treats
them as equal targets, so user_profiles.seniority_target (one level name)
becomes seniority_targets (a JSON list of 1-3 names in level order). Every
existing row holds exactly one level, so it converts to a one-element list with
no judgement call, and no confirmed score table has to be cleared.

The CHECK constraint goes with the column: a JSON list cannot be constrained
cleanly in SQL, so analysis/seniority_fit.validate_targets is the single gate
every write passes through from now on.

Revision ID: d6a8b0c2e479
Revises: c5f7a9b1d368
Create Date: 2026-09-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6a8b0c2e479"
down_revision: Union[str, Sequence[str], None] = "c5f7a9b1d368"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEVELS = "('intern', 'entry', 'mid_senior', 'senior', 'staff_principal')"


def upgrade() -> None:
    op.drop_constraint("ck_user_profiles_seniority_target", "user_profiles", type_="check")
    op.alter_column("user_profiles", "seniority_target", new_column_name="seniority_targets",
                    existing_type=sa.String(), type_=sa.JSON(), existing_nullable=False,
                    postgresql_using="json_build_array(seniority_target)")


def downgrade() -> None:
    # The first element is the one the scalar column can hold; a user who picked
    # more than one level keeps their lowest pick, which is what the old
    # single-level proposal would have scored them from.
    op.alter_column("user_profiles", "seniority_targets", new_column_name="seniority_target",
                    existing_type=sa.JSON(), type_=sa.String(), existing_nullable=False,
                    postgresql_using="seniority_targets ->> 0")
    op.create_check_constraint("ck_user_profiles_seniority_target", "user_profiles", f"seniority_target IN {LEVELS}")
