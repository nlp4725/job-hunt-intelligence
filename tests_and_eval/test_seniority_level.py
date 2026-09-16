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
from db.cloud_models import SENIORITY_LEVELS


@pytest.mark.parametrize("target, expected", [
    ("entry", {"intern": 4, "entry": 5, "mid_senior": 4, "senior": 3, "staff_principal": 2, "not_a_fit": 0, "unknown": 3}),
    ("mid_senior", {"intern": 3, "entry": 4, "mid_senior": 5, "senior": 4, "staff_principal": 3, "not_a_fit": 0, "unknown": 3}),
    ("staff_principal", {"intern": 1, "entry": 2, "mid_senior": 3, "senior": 4, "staff_principal": 5, "not_a_fit": 0, "unknown": 3}),
])
def test_the_proposal_is_five_minus_the_distance_from_the_chosen_level(target, expected):
    assert proposed_scores(target) == expected


def test_the_five_levels():
    """Decided 2026-09-16: intern, entry (0-2 years), mid_senior (2-5),
    senior (5-9), staff_principal (9+). An internship is a level, so an
    internship seeker can score it; agency and contract are separate flags."""
    assert SENIORITY_LEVELS == ("intern", "entry", "mid_senior", "senior", "staff_principal")


def test_old_local_scores_map_onto_the_new_levels():
    """The owner's old 0-5 scores (written for an entry-level candidate) seed
    job levels: 5 entry, 4 mid, 3 senior, 2 senior_plus, 1 staff under the old
    six bands, which now merge into these."""
    from db.seed_owner import SCORE_TO_LEVEL

    assert SCORE_TO_LEVEL == {5: "entry", 4: "mid_senior", 3: "senior", 2: "senior", 1: "staff_principal"}


def test_an_unknown_level_to_propose_from_is_rejected():
    with pytest.raises(ValueError):
        proposed_scores("junior")


def test_fit_is_a_lookup_in_the_users_own_table():
    table = {**proposed_scores("mid_senior"), "entry": 5, "not_a_fit": 2, "unknown": 1}
    assert seniority_fit("entry", False, table) == 5
    assert seniority_fit("staff_principal", False, table) == 3
    assert seniority_fit(None, False, table) == 1
    for level in (*SENIORITY_LEVELS, None):
        assert seniority_fit(level, True, table) == 2   # a contract posting uses the not-a-fit row


def test_a_complete_table_of_whole_scores_is_valid():
    table = {**proposed_scores("senior"), "staff_principal": 0}
    assert validate_scores(table) == table


@pytest.mark.parametrize("change", [
    lambda t: t.pop("staff_principal"),                       # a row missing
    lambda t: t.update({"apprentice": 0}),          # an unknown row
    lambda t: t.update({"mid_senior": 6}),
    lambda t: t.update({"mid_senior": -1}),
    lambda t: t.update({"mid_senior": 2.5}),
    lambda t: t.update({"mid_senior": "4"}),
    lambda t: t.update({"mid_senior": True}),
])
def test_an_incomplete_or_out_of_range_table_is_rejected(change):
    table = proposed_scores("mid_senior")
    change(table)
    with pytest.raises(ValueError):
        validate_scores(table)


class TestLevelPrompt:
    def test_the_prompt_describes_the_job_not_a_candidate(self):
        from judge.seniority_level import SENIORITY_LEVEL_PROMPT

        text = SENIORITY_LEVEL_PROMPT.lower()
        assert "my profile" not in text and "prioritizing" not in text and "score 0" not in text

    def test_every_level_and_both_flags_are_described(self):
        from judge.seniority_level import SENIORITY_LEVEL_PROMPT

        assert '"is_contract"' in SENIORITY_LEVEL_PROMPT
        assert '"is_agency"' not in SENIORITY_LEVEL_PROMPT   # agencies are filtered out before this step
        for name in SENIORITY_LEVELS:
            assert f"`{name}`" in SENIORITY_LEVEL_PROMPT, name

    def test_fractional_years_round_up(self):
        from judge.seniority_level import JobSeniorityLevel

        result = JobSeniorityLevel(evidence="1.5+ years", years_required=1.5, inferred=False,
                                   confidence="high", note=None, is_contract=False, level="entry")
        assert result.years_required == 2

    def test_null_written_as_text_counts_as_null(self):
        from judge.seniority_level import JobSeniorityLevel

        result = JobSeniorityLevel(evidence="", years_required=None, inferred=True, confidence="low",
                                   note=None, is_contract=False, level="None")
        assert result.level is None

    def test_unknown_level_names_fail_validation(self):
        from pydantic import ValidationError

        from judge.seniority_level import JobSeniorityLevel

        with pytest.raises(ValidationError):
            JobSeniorityLevel(evidence="", years_required=None, inferred=True, confidence="low",
                              note=None, is_contract=False, level="junior")

    def test_an_empty_answer_is_retried(self):
        from judge.seniority_level import JobSeniorityLevel, classify_job_seniority

        answer = JobSeniorityLevel(evidence="", years_required=None, inferred=True, confidence="low",
                                   note=None, is_contract=False, level=None)

        class FlakyModel:
            calls = 0

            def invoke(self, messages):
                self.calls += 1
                return None if self.calls == 1 else answer

        model = FlakyModel()
        assert classify_job_seniority("posting", llm=model) is answer and model.calls == 2

    def test_classification_sends_the_prompt_and_the_posting(self):
        from judge.seniority_level import SENIORITY_LEVEL_PROMPT, JobSeniorityLevel, classify_job_seniority

        answer = JobSeniorityLevel(evidence="3+ years", years_required=3, inferred=False,
                                   confidence="high", note=None, is_contract=False, level="mid_senior")

        class FakeModel:
            def invoke(self, messages):
                self.messages = messages
                return answer

        model = FakeModel()
        assert classify_job_seniority('Posting: "ML Engineer at Acme"\n\n3+ years', llm=model) is answer
        assert [m.content for m in model.messages] == [SENIORITY_LEVEL_PROMPT, 'Posting: "ML Engineer at Acme"\n\n3+ years']
