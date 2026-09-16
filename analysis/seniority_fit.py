"""Seniority Fit for one user on one job, in code (productization plan §3.3).

The job's level is classified once by judge/seniority_level.py and shared by
every user; fit is the distance between that level and the user's target.
With target "entry" this reproduces the owner's original 0-5 rubric exactly.
"""

from db.cloud_models import SENIORITY_LEVELS

UNKNOWN_LEVEL_FIT = 3   # the rubric's "nothing inferable" score


def seniority_fit(level: str | None, non_fit_reason: str | None, target: str) -> int:
    if target not in SENIORITY_LEVELS:
        raise ValueError(f"unknown seniority target: {target!r}")
    if non_fit_reason:
        return 0            # agency / contract / internship: a hard non-fit for everyone
    if level is None:
        return UNKNOWN_LEVEL_FIT
    return max(0, 5 - abs(SENIORITY_LEVELS.index(level) - SENIORITY_LEVELS.index(target)))
