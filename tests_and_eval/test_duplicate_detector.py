"""find_duplicate_job() flags a repost by near-identical JD text at the same
company. Captures from before the line-break fix have words glued together at
block boundaries ("RequirementsPythonSQL"); a repost captured after it has a
line break there. It is the same posting, so it must still be found.
"""

from db.job_writer import save_new_job
from tests_and_eval.test_jd_normalize import _detail, session  # noqa: F401  (pytest fixture)

LINES = (
    ["About the job", "We build LLM products for hospitals.", "Requirements"]
    + ["Python", "SQL", "AWS", "GCP", "Docker", "Kubernetes", "PyTorch", "Spark", "Airflow", "Kafka"] * 3
    + ["Apply now."]
)
GLUED = "".join(LINES)      # how the extension stored it before the fix
WITH_BREAKS = "\n".join(LINES)


def _save(session, linkedin_id: str, raw_text: str):
    detail = _detail(raw_text)
    detail["url"] = f"https://www.linkedin.com/jobs/view/{linkedin_id}"
    return save_new_job(session, "ml engineer", "ml_ai", linkedin_id, detail)


def test_repost_with_line_breaks_matches_an_old_glued_capture(session):  # noqa: F811
    old = _save(session, "1", GLUED)
    repost = _save(session, "2", WITH_BREAKS)
    assert repost.duplicate_of_job_id == old.id


def test_a_different_posting_at_the_same_company_is_not_a_duplicate(session):  # noqa: F811
    _save(session, "1", GLUED)
    other = _save(session, "2", "\n".join(["About the job", "We hire nurses for night shifts.", "Requirements",
                                            "RN license", "BLS certification", "Two years in an ICU"] * 3))
    assert other.duplicate_of_job_id is None
