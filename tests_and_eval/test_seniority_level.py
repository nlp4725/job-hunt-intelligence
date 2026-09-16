"""Seniority, phase 4 (productization plan §3.3).

The LLM classifies each job's level once. Each user confirms their own score
table: one 0-5 score per level, plus a "not a fit" row (internship / contract /
agency) and a "level unclear" row. Onboarding proposes the table from the level
the user picks; the user adjusts it and confirms. Seniority Fit is then a lookup
in that table. These tests need no LLM call: classification is checked with a
fake model.
"""

import pytest

from analysis.seniority_fit import proposed_scores, seniority_fit, validate_scores
from db.cloud_models import NON_FIT_REASONS, SENIORITY_LEVELS


@pytest.mark.parametrize("target, expected", [
    ("entry", {"entry": 5, "mid": 4, "senior": 3, "senior_plus": 2, "staff": 1, "principal": 0, "not_a_fit": 0, "unknown": 3}),
    ("mid", {"entry": 4, "mid": 5, "senior": 4, "senior_plus": 3, "staff": 2, "principal": 1, "not_a_fit": 0, "unknown": 3}),
    ("principal", {"entry": 0, "mid": 1, "senior": 2, "senior_plus": 3, "staff": 4, "principal": 5, "not_a_fit": 0, "unknown": 3}),
])
def test_the_proposal_is_five_minus_the_distance_from_the_chosen_level(target, expected):
    assert proposed_scores(target) == expected


def test_the_entry_proposal_reproduces_todays_rubric_scores():
    """Today's 0-5 seniority score was written for an entry-level candidate;
    the owner's scores must not move."""
    from db.seed_owner import SCORE_TO_LEVEL

    table = proposed_scores("entry")
    for score, level in SCORE_TO_LEVEL.items():
        assert seniority_fit(level, None, table) == score


def test_an_unknown_level_to_propose_from_is_rejected():
    with pytest.raises(ValueError):
        proposed_scores("junior")


def test_fit_is_a_lookup_in_the_users_own_table():
    table = {**proposed_scores("mid"), "entry": 5, "not_a_fit": 2, "unknown": 1}
    assert seniority_fit("entry", None, table) == 5
    assert seniority_fit("staff", None, table) == 2
    assert seniority_fit(None, None, table) == 1
    for reason in NON_FIT_REASONS:
        for level in (*SENIORITY_LEVELS, None):
            assert seniority_fit(level, reason, table) == 2   # a non-fit posting uses that row whatever its level


def test_a_complete_table_of_whole_scores_is_valid():
    table = {**proposed_scores("senior"), "principal": 0}
    assert validate_scores(table) == table


@pytest.mark.parametrize("change", [
    lambda t: t.pop("staff"),                       # a row missing
    lambda t: t.update({"intern": 0}),              # an unknown row
    lambda t: t.update({"mid": 6}),
    lambda t: t.update({"mid": -1}),
    lambda t: t.update({"mid": 2.5}),
    lambda t: t.update({"mid": "4"}),
    lambda t: t.update({"mid": True}),
])
def test_an_incomplete_or_out_of_range_table_is_rejected(change):
    table = proposed_scores("mid")
    change(table)
    with pytest.raises(ValueError):
        validate_scores(table)


class TestLevelPrompt:
    def test_the_prompt_describes_the_job_not_a_candidate(self):
        from judge.seniority_level import SENIORITY_LEVEL_PROMPT

        text = SENIORITY_LEVEL_PROMPT.lower()
        assert "my profile" not in text and "prioritizing" not in text and "score 0" not in text

    def test_every_level_and_non_fit_reason_is_described(self):
        from judge.seniority_level import SENIORITY_LEVEL_PROMPT

        for name in (*SENIORITY_LEVELS, *NON_FIT_REASONS):
            assert f"`{name}`" in SENIORITY_LEVEL_PROMPT, name

    def test_fractional_years_round_up(self):
        from judge.seniority_level import JobSeniorityLevel

        result = JobSeniorityLevel(evidence="1.5+ years", years_required=1.5, inferred=False,
                                   confidence="high", note=None, non_fit_reason=None, level="entry")
        assert result.years_required == 2

    def test_null_written_as_text_counts_as_null(self):
        from judge.seniority_level import JobSeniorityLevel

        result = JobSeniorityLevel(evidence="", years_required=None, inferred=True, confidence="low",
                                   note=None, non_fit_reason="null", level="None")
        assert (result.non_fit_reason, result.level) == (None, None)

    def test_unknown_level_names_fail_validation(self):
        from pydantic import ValidationError

        from judge.seniority_level import JobSeniorityLevel

        with pytest.raises(ValidationError):
            JobSeniorityLevel(evidence="", years_required=None, inferred=True, confidence="low",
                              note=None, non_fit_reason=None, level="junior")

    def test_classification_sends_the_prompt_and_the_posting(self):
        from judge.seniority_level import SENIORITY_LEVEL_PROMPT, JobSeniorityLevel, classify_job_seniority

        answer = JobSeniorityLevel(evidence="3+ years", years_required=3, inferred=False,
                                   confidence="high", note=None, non_fit_reason=None, level="mid")

        class FakeModel:
            def invoke(self, messages):
                self.messages = messages
                return answer

        model = FakeModel()
        assert classify_job_seniority('Posting: "ML Engineer at Acme"\n\n3+ years', llm=model) is answer
        assert [m.content for m in model.messages] == [SENIORITY_LEVEL_PROMPT, 'Posting: "ML Engineer at Acme"\n\n3+ years']
