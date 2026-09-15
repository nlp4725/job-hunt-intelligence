"""JD text goes through the same normalize() as resume text before skills are
extracted, everywhere JD skills come from: job_skills written at capture, and
skill_match_score used by screening. Otherwise a skill typed with a Unicode
look-alike is found on the resume side and missed on the JD side.
See docs/resume_jd_skill_pipeline.md §3.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from analysis.skill_match import skill_match_score
from db.job_writer import save_new_job
from db.models import Base, JobSkill

# U+FB01 ligature and U+2011 non-breaking hyphen, as copied from styled pages.
STYLED_JD = "Experience with ﬁne‑tuning LLMs and scikit‑learn. " * 10


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _detail(raw_text: str) -> dict:
    return {
        "url": "https://www.linkedin.com/jobs/view/1", "title": "ML Engineer", "company": "Acme",
        "industry": None, "company_size": None, "location": "Remote", "workplace_type": "Remote",
        "raw_text": raw_text, "salary_text": None, "posted_date": "1 day ago", "applicant_stats": None,
    }


def test_capture_tags_skills_from_normalized_jd_text(session):
    job = save_new_job(session, "ml engineer", "ml_ai", "1", _detail(STYLED_JD))
    skills = {name for (name,) in session.query(JobSkill.skill_name).filter(JobSkill.job_id == job.id)}
    assert {"Fine-tuning", "scikit-learn"} <= skills


def test_capture_keeps_the_original_jd_text(session):
    """normalize() only feeds the extractor; the stored posting is untouched."""
    job = save_new_job(session, "ml engineer", "ml_ai", "1", _detail(STYLED_JD))
    assert job.raw_text == STYLED_JD


def test_skill_match_score_normalizes_the_jd():
    result = skill_match_score("Fine-tuning and scikit-learn in production.", STYLED_JD)
    assert {"Fine-tuning", "scikit-learn"} <= set(result["matched_skills"])
