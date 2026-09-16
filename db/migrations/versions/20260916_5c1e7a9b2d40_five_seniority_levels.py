"""five seniority levels: intern, entry, mid_senior, senior, staff_principal

Decided 2026-09-16. Existing rows are converted: an internship becomes the
level `intern` (no longer a non-fit reason); mid -> mid_senior; senior and
senior_plus -> senior; staff and principal -> staff_principal. Confirmed score
tables were written for the old rows, so they are cleared and each user falls
back to the proposal for their (converted) level until they confirm again.

Revision ID: 5c1e7a9b2d40
Revises: 82e38a2e4655
Create Date: 2026-09-16
"""

from typing import Sequence, Union

from alembic import op

revision: str = "5c1e7a9b2d40"
down_revision: Union[str, Sequence[str], None] = "82e38a2e4655"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_LEVELS = "('intern', 'entry', 'mid_senior', 'senior', 'staff_principal')"
OLD_LEVELS = "('entry', 'mid', 'senior', 'senior_plus', 'staff', 'principal')"
TO_NEW = ("CASE {col} WHEN 'mid' THEN 'mid_senior' WHEN 'senior_plus' THEN 'senior' "
          "WHEN 'staff' THEN 'staff_principal' WHEN 'principal' THEN 'staff_principal' ELSE {col} END")
TO_OLD = "CASE {col} WHEN 'mid_senior' THEN 'mid' WHEN 'staff_principal' THEN 'staff' ELSE {col} END"


def _drop_checks() -> None:
    op.drop_constraint("ck_job_seniority_level", "job_seniority", type_="check")
    op.drop_constraint("ck_job_seniority_non_fit_reason", "job_seniority", type_="check")
    op.drop_constraint("ck_user_profiles_seniority_target", "user_profiles", type_="check")


def upgrade() -> None:
    _drop_checks()
    op.execute("UPDATE job_seniority SET level = 'intern', non_fit_reason = NULL WHERE non_fit_reason = 'internship'")
    op.execute(f"UPDATE job_seniority SET level = {TO_NEW.format(col='level')}")
    op.execute(f"UPDATE user_profiles SET seniority_target = {TO_NEW.format(col='seniority_target')}")
    op.execute("UPDATE user_profiles SET seniority_scores = NULL")
    op.create_check_constraint("ck_job_seniority_level", "job_seniority", f"level IN {NEW_LEVELS}")
    op.create_check_constraint("ck_job_seniority_non_fit_reason", "job_seniority", "non_fit_reason IN ('agency', 'contract')")
    op.create_check_constraint("ck_user_profiles_seniority_target", "user_profiles", f"seniority_target IN {NEW_LEVELS}")


def downgrade() -> None:
    _drop_checks()
    op.execute("UPDATE job_seniority SET level = 'entry', non_fit_reason = 'internship' WHERE level = 'intern'")
    op.execute(f"UPDATE job_seniority SET level = {TO_OLD.format(col='level')}")
    op.execute("UPDATE user_profiles SET seniority_target = 'entry' WHERE seniority_target = 'intern'")
    op.execute(f"UPDATE user_profiles SET seniority_target = {TO_OLD.format(col='seniority_target')}")
    op.execute("UPDATE user_profiles SET seniority_scores = NULL")
    op.create_check_constraint("ck_job_seniority_level", "job_seniority", f"level IN {OLD_LEVELS}")
    op.create_check_constraint("ck_job_seniority_non_fit_reason", "job_seniority",
                               "non_fit_reason IN ('agency', 'contract', 'internship')")
    op.create_check_constraint("ck_user_profiles_seniority_target", "user_profiles", f"seniority_target IN {OLD_LEVELS}")
