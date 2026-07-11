"""
Builds/uploads the "test_seniority_n_expertise" LangSmith dataset: the exact
same 20 jobs, in the exact same order, as tests_and_eval/test_seniority and
tests_and_eval/test_expertise's own datasets — this just pairs their two
already-confirmed human-labeled scores per job into one dataset with two
output labels (outputs.seniority_score, outputs.expertise_score), so a
combined-call target can be evaluated against both ground truths at once.

Ground truth is NOT re-labeled here — both scores are copied verbatim from
tests_and_eval/test_seniority/build_dataset.py and
tests_and_eval/test_expertise/build_dataset.py.

Re-running this script is safe: create_dataset() is a no-op if the dataset
already exists, and create_examples() appends rather than duplicating on
exact dataset_name + example match.
"""

from langsmith import Client

from db.models import Job
from db.session import get_session
from tests_and_eval.test_seniority_n_expertise.common import get_job_posting

DATASET_NAME = "test_seniority_n_expertise"

# (job_id, seniority_score, expertise_score, seniority_category, expertise_category)
# — seniority_score/seniority_category from test_seniority/build_dataset.py,
# expertise_score/expertise_category from test_expertise/build_dataset.py,
# paired by job_id (both source lists cover the same 20 jobs in the same order).
EXAMPLES = [
    (188, 4, 4, "clean_anchor", "no_domain_clear_capability"),
    (171, 5, 0, "clean_anchor", "recruiter_rule_1a"),
    (710, 3, 0, "clean_anchor", "recruiter_rule_1a"),
    (99, 4, 0, "range_parsing", "recruiter_rule_1b"),
    (337, 2, 5, "range_parsing", "domain_boundary_call"),
    (950, 2, 0, "clean_anchor", "recruiter_rule_1b"),
    (6, 3, 4, "years_vs_responsibility_tension", "no_domain_clear_capability"),
    (47, 1, 5, "years_vs_responsibility_tension", "clean_domain_and_capability"),
    (1032, 1, 3, "years_vs_responsibility_tension", "generic_no_edge"),
    (128, 1, 5, "multi_number_conflict", "clean_domain_and_capability"),
    (67, 0, 1, "multi_number_conflict", "domain_match_but_w3_governs"),
    (84, 1, 5, "multi_number_conflict", "clean_domain_and_capability"),
    (141, 1, 3, "no_years_inference", "ambiguous_training_type_rule_4"),
    (13, 0, 0, "multi_number_conflict", "recruiter_rule_1a"),
    (122, 5, 4, "title_vs_duty_mismatch", "incidental_domain_mention_not_credited"),
    (381, 0, 3, "agency_rule", "too_vague_rule_6"),
    (1581, 5, 0, "no_years_inference", "recruiter_rule_1a"),
    (1617, 5, 4, "range_parsing", "no_domain_clear_capability"),
    (34, 5, 4, "title_vs_duty_mismatch", "domain_boundary_call"),
    (663, 0, 4, "clean_anchor", "domain_boundary_call"),
]

# Clean in both source datasets (intersection of test_seniority's and
# test_expertise's own _CLEAN_JOB_IDS) — everything else required a real
# judgment call for at least one of the two rubrics.
_CLEAN_JOB_IDS = {188, 171, 710, 99, 950}


def build_dataset() -> None:
    client = Client()

    if not client.has_dataset(dataset_name=DATASET_NAME):
        client.create_dataset(
            dataset_name=DATASET_NAME,
            description=(
                "Combined-call eval: seniority_fit + expertise_match scored in one LLM "
                "call against the same 20 real JDs used by test_seniority and "
                "test_expertise individually. Ground truth is the same human labels "
                "from both of those datasets, paired by job_id. Exists to check whether "
                "combining the two rubrics into one call costs accuracy versus scoring "
                "them separately."
            ),
        )

    session = get_session()
    try:
        examples = []
        for job_id, seniority_score, expertise_score, seniority_category, expertise_category in EXAMPLES:
            job = session.get(Job, job_id)
            examples.append({
                "inputs": {"job_id": job_id, "posting": get_job_posting(job_id)},
                "outputs": {"seniority_score": seniority_score, "expertise_score": expertise_score},
                "metadata": {
                    "title": job.title,
                    "company_name": job.company_name,
                    "seniority_category": seniority_category,
                    "expertise_category": expertise_category,
                },
                "split": "clean" if job_id in _CLEAN_JOB_IDS else "ambiguous",
            })
    finally:
        session.close()

    client.create_examples(dataset_name=DATASET_NAME, examples=examples)
    print(f"Uploaded {len(examples)} examples to dataset '{DATASET_NAME}'.")


if __name__ == "__main__":
    build_dataset()
