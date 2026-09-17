"""Seniority Fit for one user on one job, in code (productization plan §3.3).

The job's level is classified once by judge/seniority_level.py and shared by
every user. Each user confirms their own score table: a 0-5 score for every
level, one for contract postings ("not_a_fit"; agencies are filtered out earlier) and
one for postings whose level is unclear. Onboarding proposes the table from the
levels the user picks — up to three, all equal targets (decided 2026-09-17,
because someone open to mid_senior and senior is not "really" one of the two):
every picked level scores five, and every other level five minus its distance
to the *nearest* pick. The user adjusts the proposal and confirms it. Fit is a
lookup in that table.

Levels (decided 2026-09-16): intern, entry [0, 2), mid_senior [2, 5), senior [5, 9),
staff_principal [9+).
"""

from db.cloud_models import SENIORITY_LEVELS

NOT_A_FIT = "not_a_fit"
UNKNOWN = "unknown"
SCORE_KEYS = (*SENIORITY_LEVELS, NOT_A_FIT, UNKNOWN)
UNKNOWN_LEVEL_SCORE = 3   # the rubric's "nothing inferable" score
MAX_TARGETS = 3           # how many levels onboarding lets a user pick


def validate_targets(targets) -> list[str]:
    """The one gate every seniority target list passes through, wherever it
    comes from (onboarding, Settings, a seed script): 1 to MAX_TARGETS distinct
    level names, returned in SENIORITY_LEVELS order so a stored list never
    depends on the order the user happened to click in. The column is JSON and
    cannot carry a CHECK constraint, so this function is the only thing
    standing between the API and the database — callers validate before writing
    anything, and proposed_scores runs it too, so a list that reached the
    database invalid would still be caught on the way out.

    Raises ValueError with a message a person can read; the API returns it as-is."""
    if isinstance(targets, str) or not isinstance(targets, (list, tuple)):
        raise ValueError("seniority levels must be a list of level names")
    if not targets:
        raise ValueError("pick at least one seniority level")
    if len(targets) > MAX_TARGETS:
        raise ValueError(f"pick at most {MAX_TARGETS} seniority levels")
    unknown = [t for t in targets if t not in SENIORITY_LEVELS]
    if unknown:
        raise ValueError(f"unknown seniority level: {', '.join(repr(t) for t in unknown)}")
    if len(set(targets)) != len(targets):
        raise ValueError("pick each seniority level only once")
    return sorted(set(targets), key=SENIORITY_LEVELS.index)


def proposed_scores(targets) -> dict[str, int]:
    """Five for every picked level, and for the rest five minus the distance to
    the nearest pick. Picking mid_senior and senior, for instance, proposes
    intern 3, entry 4, mid_senior 5, senior 5, staff_principal 4 — the same
    table a single pick produced, except that the range between the picks is
    flat at five instead of sloping away from one level."""
    picked = [SENIORITY_LEVELS.index(target) for target in validate_targets(targets)]
    table = {level: max(0, 5 - min(abs(i - p) for p in picked)) for i, level in enumerate(SENIORITY_LEVELS)}
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


def seniority_fit(level: str | None, is_contract: bool, scores: dict[str, int]) -> int:
    if is_contract:
        return scores[NOT_A_FIT]
    if level is None:
        return scores[UNKNOWN]
    return scores[level]
