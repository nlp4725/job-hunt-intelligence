"""Seniority, phase 4 (productization plan §3.3).

The LLM classifies each job's level once; every user's Seniority Fit is then
computed in code from that level and their own target. These tests need no
LLM call: the fit table is plain code, and classification is checked with a
fake model.
"""

import pytest

from analysis.seniority_fit import seniority_fit
from db.cloud_models import NON_FIT_REASONS, SENIORITY_LEVELS


@pytest.mark.parametrize("target, expected", [
    ("entry", {"entry": 5, "mid": 4, "senior": 3, "senior_plus": 2, "staff": 1, "principal": 0}),
    ("senior", {"entry": 3, "mid": 4, "senior": 5, "senior_plus": 4, "staff": 3, "principal": 2}),
    ("principal", {"entry": 0, "mid": 1, "senior": 2, "senior_plus": 3, "staff": 4, "principal": 5}),
])
def test_fit_is_five_minus_the_distance_between_levels(target, expected):
    assert {level: seniority_fit(level, None, target) for level in SENIORITY_LEVELS} == expected


def test_an_entry_target_reproduces_todays_rubric_scores():
    """Today's 0-5 seniority score was written for an entry-level candidate;
    the owner's scores must not move."""
    from db.seed_owner import SCORE_TO_LEVEL

    for score, level in SCORE_TO_LEVEL.items():
        assert seniority_fit(level, None, "entry") == score
    assert seniority_fit("principal", None, "entry") == 0


@pytest.mark.parametrize("target", SENIORITY_LEVELS)
def test_non_fit_postings_score_zero_whatever_the_level_or_target(target):
    for reason in NON_FIT_REASONS:
        assert seniority_fit(target, reason, target) == 0
        assert seniority_fit(None, reason, target) == 0


@pytest.mark.parametrize("target", SENIORITY_LEVELS)
def test_an_unknown_level_scores_three_for_every_target(target):
    assert seniority_fit(None, None, target) == 3


def test_an_unknown_target_is_rejected():
    with pytest.raises(ValueError):
        seniority_fit("mid", None, "junior")


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
