"""Phase 3: resume ingest and per-user Skill Match in the cloud.

A user has one active resume: the one their latest profile version points at.
Every upload is a new resume version; confirming or editing its skills
activates it and rescores the user from stored skill sets (no LLM).

Postgres tests need JHI_TEST_POSTGRES_URL (see test_cloud_db.py).
"""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from analysis.skill_match import skill_match_from_skills, skill_match_score
from db.job_writer import save_new_job
from db.models import Job, JobSkill, Resume
from resume.pii import KnownIdentity, redact_pii
from resume.resume_text import UnsupportedResume
from tests_and_eval.test_cloud_db import PG_URL, _upgrade, needs_pg, pg_engine  # noqa: F401  (fixture)
from tests_and_eval.test_cloud_users import _local_with_statuses, _user
from tests_and_eval.test_jd_normalize import _detail
from tests_and_eval.test_resume_text import _pdf_bytes

RESUME_TEXT = (
    "Jane Doe\n"
    "jane@example.com | 555-123-4567\n"
    "SUMMARY\n"
    "ML engineer.\n"
    "SKILLS\n"
    "Python, SQL, Docker, LangGraph\n"
    "EXPERIENCE\n"
    "Built RAG systems on AWS.\n"
)
JD_A = "Python SQL Docker LangGraph RAG AWS " * 10
JD_B = "Kubernetes Spark Scala Java " * 10


@pytest.fixture
def cipher():
    from resume.store import ResumeCipher

    return ResumeCipher(Fernet.generate_key())


@pytest.fixture
def store(tmp_path):
    from resume.store import LocalFileStore

    return LocalFileStore(tmp_path / "files")


def test_redaction_with_no_known_name_leaves_the_text_intact():
    """A user without a display name must not turn every gap in the text into [NAME]."""
    text = "SKILLS\nPython, SQL"
    assert redact_pii(text, KnownIdentity(name="", email="a@example.com")) == text


class TestStore:
    def test_ciphertext_hides_the_text_and_decrypts_back(self, cipher):
        secret = b"Built RAG systems in Python"
        sealed = cipher.encrypt(secret)
        assert b"Python" not in sealed and cipher.decrypt(sealed) == secret

    def test_files_stay_under_the_store_root(self, store):
        from resume.store import resume_storage_key

        key = resume_storage_key(7, 2, "../../etc/my cv (final).pdf")
        assert key == "users/7/resumes/v2/my_cv__final_.pdf"
        store.put(key, b"data")
        assert store.get(key) == b"data"
        with pytest.raises(ValueError):
            store.put("../outside.pdf", b"data")


@pytest.fixture
def db(pg_engine):  # noqa: F811
    _upgrade(PG_URL)
    session = sessionmaker(bind=pg_engine)()
    yield session
    session.rollback()
    session.close()


def _jobs(db) -> dict[str, Job]:
    """Two distinct jobs and a repost of the first (duplicates are not scored)."""
    return {linkedin_id: save_new_job(db, "llm remote", "ml_ai", linkedin_id, _detail(text))
            for linkedin_id, text in (("1", JD_A), ("2", JD_B), ("3", JD_A))}


def _job_skills(db, job: Job) -> set[str]:
    return {name for (name,) in db.query(JobSkill.skill_name).filter(JobSkill.job_id == job.id)}


def _profile(db, user_id: int, version: int = 1, **kw):
    from db.cloud_models import UserProfile

    profile = UserProfile(user_id=user_id, version=version,
                          seniority_targets=kw.pop("seniority_targets", ["entry"]), **kw)
    db.add(profile)
    db.flush()
    return profile


def _latest_profile(db, user_id: int):
    from db.cloud_models import UserProfile

    return db.query(UserProfile).filter_by(user_id=user_id).order_by(UserProfile.version.desc()).first()


def _scores(db, user_id: int) -> dict:
    from db.cloud_models import UserJobScore

    return {job_id: s for s, job_id in db.query(UserJobScore, Job.job_id).join(Job, Job.id == UserJobScore.job_id)
            .filter(UserJobScore.user_id == user_id)}


@needs_pg
class TestAddResume:
    def test_upload_stores_an_encrypted_redacted_version(self, db, store, cipher):
        from analysis.taxonomy_version import taxonomy_version
        from resume.ingest import add_resume

        user = _user(db, "jane@example.com", display_name="Jane Doe")
        original = _pdf_bytes(RESUME_TEXT)
        resume = add_resume(db, user, "cv.pdf", original, store, cipher)

        assert (resume.version, resume.skills_confirmed, resume.original_filename) == (1, None, "cv.pdf")
        assert {"Python", "SQL", "Docker", "LangGraph", "RAG", "AWS"} <= set(resume.skills_extracted)
        assert resume.taxonomy_version == taxonomy_version()
        assert "jane" not in resume.redacted_text.lower() and "555" not in resume.redacted_text
        assert b"Python" not in resume.text_encrypted
        assert "Built RAG systems on AWS." in " ".join(cipher.decrypt(resume.text_encrypted).decode().split())
        stored = store.get(resume.storage_key)
        assert stored != original and cipher.decrypt(stored) == original

    def test_each_upload_is_a_new_version(self, db, store, cipher):
        from resume.ingest import add_resume

        user = _user(db)
        versions = [add_resume(db, user, "cv.txt", RESUME_TEXT.encode(), store, cipher).version for _ in range(2)]
        assert versions == [1, 2]

    def test_an_unreadable_file_stores_nothing(self, db, store, cipher):
        from db.cloud_models import UserResume
        from resume.ingest import add_resume

        user = _user(db)
        with pytest.raises(UnsupportedResume):
            add_resume(db, user, "cv.pages", b"not a resume", store, cipher)
        assert db.query(UserResume).count() == 0
        assert not store.root.exists() or not any(store.root.rglob("*.*"))


@needs_pg
class TestConfirmSkills:
    def test_first_confirmation_activates_the_resume_and_scores_every_job(self, db, store, cipher):
        from resume.ingest import add_resume, confirm_skills

        user = _user(db)
        _profile(db, user.id)
        jobs = _jobs(db)
        resume = add_resume(db, user, "cv.txt", RESUME_TEXT.encode(), store, cipher)

        confirm_skills(db, user.id, resume.id, ["SQL", "Python", "Python"])

        assert resume.skills_confirmed == ["Python", "SQL"]
        profile = _latest_profile(db, user.id)
        assert (profile.version, profile.resume_id, profile.seniority_targets) == (2, resume.id, ["entry"])
        scores = _scores(db, user.id)
        assert set(scores) == {"1", "2"}   # the repost is not scored
        for linkedin_id, score in scores.items():
            expected = skill_match_from_skills(_job_skills(db, jobs[linkedin_id]), {"Python", "SQL"})
            assert (score.skill_score, score.skill_matched, score.skill_group_matched, score.skill_missing) == (
                expected["score"], expected["matched_skills"], expected["group_matched_skills"], expected["missing_skills"])
            assert (score.profile_version, score.seniority_fit, score.total_score) == (2, None, None)

    def test_editing_confirmed_skills_writes_a_new_version_and_rescores(self, db, store, cipher):
        from db.cloud_models import UserJobScore
        from resume.ingest import add_resume, confirm_skills

        user = _user(db)
        _profile(db, user.id)
        _jobs(db)
        first = add_resume(db, user, "cv.txt", RESUME_TEXT.encode(), store, cipher)
        confirm_skills(db, user.id, first.id, ["Python"])

        edited = confirm_skills(db, user.id, first.id, ["Python", "SQL", "Docker", "LangGraph", "RAG", "AWS"])

        assert first.skills_confirmed == ["Python"]
        assert (edited.version, edited.storage_key, edited.text_encrypted) == (2, first.storage_key, first.text_encrypted)
        assert _latest_profile(db, user.id).resume_id == edited.id
        scores = _scores(db, user.id)
        assert scores["1"].skill_score == 5 and scores["1"].profile_version == 3
        assert db.query(UserJobScore).filter_by(user_id=user.id).count() == 2

    def test_without_a_profile_skills_are_saved_but_nothing_is_scored(self, db, store, cipher):
        from resume.ingest import add_resume, confirm_skills

        user = _user(db)
        _jobs(db)
        resume = add_resume(db, user, "cv.txt", RESUME_TEXT.encode(), store, cipher)
        confirm_skills(db, user.id, resume.id, ["Python"])
        assert resume.skills_confirmed == ["Python"]
        assert _latest_profile(db, user.id) is None and _scores(db, user.id) == {}

    def test_unknown_skill_names_are_rejected(self, db, store, cipher):
        from resume.ingest import add_resume, confirm_skills

        user = _user(db)
        resume = add_resume(db, user, "cv.txt", RESUME_TEXT.encode(), store, cipher)
        with pytest.raises(ValueError, match="not in the skill list"):
            confirm_skills(db, user.id, resume.id, ["Python", "Basket Weaving"])

    def test_cannot_confirm_someone_elses_resume(self, db, store, cipher):
        from resume.ingest import add_resume, confirm_skills

        owner, other = _user(db, "a@example.com"), _user(db, "b@example.com")
        resume = add_resume(db, owner, "cv.txt", RESUME_TEXT.encode(), store, cipher)
        with pytest.raises(LookupError):
            confirm_skills(db, other.id, resume.id, ["Python"])


ML_RESUME = "SKILLS\nPython, LLM, RAG, PyTorch\n"
PM_RESUME = "SKILLS\nRoadmapping, A/B Testing, SQL\n"


@needs_pg
class TestImportOwnerResumes:
    @pytest.fixture
    def imported(self, pg_engine, tmp_path, store, cipher):  # noqa: F811
        from db.copy_to_cloud import copy_sqlite_to_postgres
        from db.import_owner_resumes import import_owner_resumes
        from db.seed_owner import seed_owner

        local = _local_with_statuses(tmp_path)
        engine = create_engine(f"sqlite:///{local}")
        with sessionmaker(bind=engine)() as session:
            session.add_all([Resume(content=ML_RESUME, original_filename="ml.pdf", track="ml_ai"),
                             Resume(content=PM_RESUME, original_filename="pm.pdf", track="pm")])
            session.commit()
        engine.dispose()
        _upgrade(PG_URL)
        copy_sqlite_to_postgres(local, PG_URL)
        seed_owner(PG_URL, email="owner@example.com", display_name="Owner")
        report = import_owner_resumes(PG_URL, store, cipher)
        session = sessionmaker(bind=pg_engine)()
        yield report, session
        session.close()

    def test_both_resumes_are_stored_and_the_ml_ai_one_is_active(self, imported):
        from db.cloud_models import UserResume

        _, db = imported
        resumes = db.query(UserResume).filter_by(user_id=1).order_by(UserResume.version).all()
        assert [("RAG" in r.skills_confirmed, "Roadmapping" in r.skills_confirmed) for r in resumes] == [
            (False, True), (True, False)]   # pm first, then ml_ai, so ml_ai ends up active
        assert _latest_profile(db, 1).resume_id == resumes[1].id

    def test_owner_scores_match_text_scoring_of_the_ml_ai_resume(self, imported):
        report, db = imported
        scores = _scores(db, 1)
        assert scores and report.scored_jobs == len(scores)
        for linkedin_id, score in scores.items():
            job = db.query(Job).filter_by(job_id=linkedin_id).one()
            assert score.skill_score == skill_match_score(ML_RESUME, job.raw_text)["score"], linkedin_id

    def test_refuses_to_import_twice(self, imported, store, cipher):
        from db.import_owner_resumes import import_owner_resumes

        with pytest.raises(RuntimeError, match="already has resumes"):
            import_owner_resumes(PG_URL, store, cipher)
