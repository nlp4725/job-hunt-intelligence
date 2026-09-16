"""Phase 4 in the cloud: job levels and each user's confirmed score table feed
their Seniority Fit and total.

Postgres tests need JHI_TEST_POSTGRES_URL (see test_cloud_db.py).
"""

import pytest

from analysis.seniority_fit import proposed_scores
from db.models import Job
from judge.seniority_level import JobSeniorityLevel
from tests_and_eval.test_cloud_db import PG_URL, needs_pg
from tests_and_eval.test_cloud_resumes import (  # noqa: F401  (fixtures)
    JD_A, JD_B, RESUME_TEXT, _jobs, _latest_profile, _profile, _scores, cipher, db, pg_engine, store,
)
from tests_and_eval.test_cloud_users import _user


def _level(level=None, non_fit_reason=None) -> JobSeniorityLevel:
    return JobSeniorityLevel(evidence="e", years_required=None, inferred=True, confidence="medium",
                             note=None, non_fit_reason=non_fit_reason, level=level)


def _scored_user(db, store, cipher, target="entry", **profile):
    """A user with a confirmed resume (Python, SQL) and a profile at `target`."""
    from resume.ingest import add_resume, confirm_skills

    user = _user(db)
    _profile(db, user.id, seniority_target=target, **profile)
    resume = add_resume(db, user, "cv.txt", RESUME_TEXT.encode(), store, cipher)
    confirm_skills(db, user.id, resume.id, ["Python", "SQL"])
    return user


@needs_pg
class TestScoresUseJobLevels:
    def test_fit_and_total_come_from_the_job_level_and_the_users_table(self, db, store, cipher):
        from db.cloud_models import JobSeniority

        jobs = _jobs(db)
        db.add_all([JobSeniority(job_id=jobs["1"].id, level="mid"),
                    JobSeniority(job_id=jobs["2"].id, level="senior", non_fit_reason="contract")])
        table = {**proposed_scores("entry"), "mid": 5, "not_a_fit": 2}
        user = _scored_user(db, store, cipher, seniority_scores=table)

        scores = _scores(db, user.id)
        assert (scores["1"].seniority_fit, scores["1"].total_score) == (5, scores["1"].skill_score + 5)
        assert (scores["2"].seniority_fit, scores["2"].total_score) == (2, scores["2"].skill_score + 2)

    def test_a_profile_without_a_confirmed_table_uses_the_proposal_for_its_level(self, db, store, cipher):
        from db.cloud_models import JobSeniority

        jobs = _jobs(db)
        db.add(JobSeniority(job_id=jobs["1"].id, level="mid"))
        user = _scored_user(db, store, cipher, target="entry")
        assert _scores(db, user.id)["1"].seniority_fit == 4

    def test_a_job_without_a_level_has_no_fit_or_total_yet(self, db, store, cipher):
        _jobs(db)
        user = _scored_user(db, store, cipher)
        score = _scores(db, user.id)["1"]
        assert (score.seniority_fit, score.total_score) == (None, None)
        assert score.skill_score is not None


@needs_pg
class TestConfirmSeniorityScores:
    def test_confirming_saves_the_table_on_a_new_profile_version_and_rescores(self, db, store, cipher):
        from analysis.user_scoring import set_seniority_scores
        from db.cloud_models import JobSeniority

        jobs = _jobs(db)
        db.add(JobSeniority(job_id=jobs["1"].id, level="mid"))
        user = _scored_user(db, store, cipher)
        before = _latest_profile(db, user.id)
        table = {**proposed_scores("entry"), "mid": 5}

        set_seniority_scores(db, user.id, table)

        after = _latest_profile(db, user.id)
        assert (after.version, after.seniority_scores, after.seniority_target, after.resume_id) == (
            before.version + 1, table, "entry", before.resume_id)
        assert _scores(db, user.id)["1"].seniority_fit == 5

    def test_a_confirmed_table_is_never_edited_in_place(self, db, store, cipher):
        from analysis.user_scoring import set_seniority_scores

        user = _scored_user(db, store, cipher)
        first = set_seniority_scores(db, user.id, proposed_scores("entry"))
        set_seniority_scores(db, user.id, proposed_scores("mid"), target="mid")

        db.refresh(first)
        assert first.seniority_scores == proposed_scores("entry") and first.seniority_target == "entry"
        assert _latest_profile(db, user.id).seniority_target == "mid"

    def test_an_invalid_table_writes_nothing(self, db, store, cipher):
        from analysis.user_scoring import set_seniority_scores

        user = _scored_user(db, store, cipher)
        version = _latest_profile(db, user.id).version
        with pytest.raises(ValueError):
            set_seniority_scores(db, user.id, {**proposed_scores("entry"), "mid": 9})
        with pytest.raises(ValueError):
            set_seniority_scores(db, user.id, proposed_scores("entry"), target="junior")
        assert _latest_profile(db, user.id).version == version


@needs_pg
class TestPickLevelThenConfirmScores:
    """Onboarding step 3 saves the level; step 4 shows the proposed table for
    that level and the user agrees or adjusts it."""

    def test_saving_the_level_alone_scores_with_its_proposal_until_confirmed(self, db, store, cipher):
        from analysis.user_scoring import set_seniority_scores, set_seniority_target
        from db.cloud_models import JobSeniority

        jobs = _jobs(db)
        db.add(JobSeniority(job_id=jobs["1"].id, level="senior"))
        user = _scored_user(db, store, cipher)
        before = _latest_profile(db, user.id)

        set_seniority_target(db, user.id, "mid")

        picked = _latest_profile(db, user.id)
        assert (picked.version, picked.seniority_target, picked.seniority_scores, picked.resume_id) == (
            before.version + 1, "mid", None, before.resume_id)
        assert _scores(db, user.id)["1"].seniority_fit == 4          # the mid proposal

        set_seniority_scores(db, user.id, {**proposed_scores("mid"), "senior": 5})
        assert _scores(db, user.id)["1"].seniority_fit == 5          # the confirmed table

    def test_changing_the_level_later_clears_the_table_until_it_is_confirmed_again(self, db, store, cipher):
        from analysis.user_scoring import set_seniority_scores, set_seniority_target

        user = _scored_user(db, store, cipher)
        set_seniority_scores(db, user.id, {**proposed_scores("entry"), "mid": 5})

        set_seniority_target(db, user.id, "staff")

        assert _latest_profile(db, user.id).seniority_scores is None

    def test_an_unknown_level_writes_nothing(self, db, store, cipher):
        from analysis.user_scoring import set_seniority_target

        user = _scored_user(db, store, cipher)
        version = _latest_profile(db, user.id).version
        with pytest.raises(ValueError):
            set_seniority_target(db, user.id, "junior")
        assert _latest_profile(db, user.id).version == version


@needs_pg
class TestClassifyMissing:
    def test_only_unclassified_non_duplicate_jobs_are_sent_and_failures_are_counted(self, db, store, cipher):
        from db.classify_job_seniority import PROMPT_VERSION, classify_missing
        from db.cloud_models import JobSeniority
        from db.job_writer import save_new_job
        from tests_and_eval.test_jd_normalize import _detail

        jobs = _jobs(db)                                      # 1: JD_A, 2: JD_B, 3: repost of 1
        already = save_new_job(db, "llm remote", "ml_ai", "4", _detail("Rust Go distributed systems " * 10))
        db.add(JobSeniority(job_id=already.id, level="staff", prompt_version="earlier"))
        user = _scored_user(db, store, cipher)
        db.commit()

        sent = []

        def fake_classify(posting: str) -> JobSeniorityLevel:
            sent.append(posting)
            if JD_B.strip() in posting:
                raise RuntimeError("model unavailable")
            return _level("mid")

        report = classify_missing(PG_URL, classify=fake_classify, workers=2)

        assert (report.classified, report.failed, report.rescored_users) == (1, 1, 1)
        assert len(sent) == 2 and all(JD_A.strip() in p or JD_B.strip() in p for p in sent)
        db.expire_all()
        rows = {job_id: s for s, job_id in db.query(JobSeniority, Job.job_id).join(Job, Job.id == JobSeniority.job_id)}
        assert set(rows) == {"1", "4"}
        assert (rows["1"].level, rows["1"].prompt_version) == ("mid", PROMPT_VERSION)
        assert (rows["4"].level, rows["4"].prompt_version) == ("staff", "earlier")
        assert _scores(db, user.id)["1"].seniority_fit == 4

    def test_limit_caps_how_many_jobs_are_sent(self, db, store, cipher):
        from db.classify_job_seniority import classify_missing

        _jobs(db)
        db.commit()
        sent = []
        report = classify_missing(PG_URL, classify=lambda p: sent.append(p) or _level("entry"), limit=1)
        assert report.classified == 1 and len(sent) == 1
