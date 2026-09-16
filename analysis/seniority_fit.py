"""Seniority Fit for one user on one job, in code (productization plan §3.3).

The job's level is classified once by judge/seniority_level.py and shared by
every user. Each user confirms their own score table: a 0-5 score for every
level, one for postings that are not a fit (agency / contract) and
one for postings whose level is unclear. Onboarding proposes the table from the
level the user picks (five minus the distance from it); the user adjusts it and
confirms. Fit is a lookup in that table.

Levels (decided 2026-09-16): intern, entry [0, 2), mid_senior [2, 5), senior [5, 9),
staff_principal [9+).
"""

from db.cloud_models import SENIORITY_LEVELS

NOT_A_FIT = "not_a_fit"
UNKNOWN = "unknown"
SCORE_KEYS = (*SENIORITY_LEVELS, NOT_A_FIT, UNKNOWN)
UNKNOWN_LEVEL_SCORE = 3   # the rubric's "nothing inferable" score


def proposed_scores(target: str) -> dict[str, int]:
    if target not in SENIORITY_LEVELS:
        raise ValueError(f"unknown seniority level: {target!r}")
    table = {level: max(0, 5 - abs(i - SENIORITY_LEVELS.index(target))) for i, level in enumerate(SENIORITY_LEVELS)}
    return {**table, NOT_A_FIT: 0, UNKNOWN: UNKNOWN_LEVEL_SCORE}


def validate_scores(scores: dict) -> dict[str, int]:
    """Every row present, nothing else, each a whole number 0-5."""
    if set(scores) != set(SCORE_KEYS):
        missing, extra = set(SCORE_KEYS) - set(scores), set(scores) - set(SCORE_KEYS)
        raise ValueError(f"score table rows: missing {sorted(missing)}, unexpected {sorted(extra)}")
    for key, value in scores.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5:
            raise ValueError(f"score for {key!r} must be a whole number from 0 to 5, got {value!r}")
    return dict(scores)


def seniority_fit(level: str | None, non_fit_reason: str | None, scores: dict[str, int]) -> int:
    if non_fit_reason:
        return scores[NOT_A_FIT]
    if level is None:
        return scores[UNKNOWN]
    return scores[level]
