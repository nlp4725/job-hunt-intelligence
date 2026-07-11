"""
Builds/uploads the "test_expertise" LangSmith dataset: the same 20 jobs
as tests_and_eval/test_seniority/build_dataset.py (chosen for that
rubric's edge cases), re-labeled here for expertise_match. Ground truth
(`outputs.score`) is human-labeled, confirmed one job at a time — five
scores changed from 0 to a real score (or vice versa) purely because of
the recruiter-detection rule (171, 710, 99, 13, 950, 1581), which has
nothing to do with seniority_fit's own concerns.

Re-running this script is safe: create_dataset() is a no-op if the
dataset already exists, and create_examples() appends rather than
duplicating on exact dataset_name + example match.
"""

from langsmith import Client

from db.models import Job
from db.session import get_session
from tests_and_eval.test_expertise.common import get_job_posting

DATASET_NAME = "test_expertise"

# (job_id, human-labeled score, category)
EXAMPLES = [
    (188, 4, "no_domain_clear_capability"),
    (171, 0, "recruiter_rule_1a"),
    (710, 0, "recruiter_rule_1a"),
    (99, 0, "recruiter_rule_1b"),
    (337, 5, "domain_boundary_call"),
    (950, 0, "recruiter_rule_1b"),
    (6, 4, "no_domain_clear_capability"),
    (47, 5, "clean_domain_and_capability"),
    (1032, 3, "generic_no_edge"),
    (128, 5, "clean_domain_and_capability"),
    (67, 1, "domain_match_but_w3_governs"),
    (84, 5, "clean_domain_and_capability"),
    (141, 3, "ambiguous_training_type_rule_4"),
    (13, 0, "recruiter_rule_1a"),
    (122, 4, "incidental_domain_mention_not_credited"),
    (381, 3, "too_vague_rule_6"),
    (1581, 0, "recruiter_rule_1a"),
    (1617, 4, "no_domain_clear_capability"),
    (34, 4, "domain_boundary_call"),
    (663, 4, "domain_boundary_call"),
]

# "clean" vs "ambiguous" split, per whether labeling required real
# judgment calls / back-and-forth vs. a single clean rule application.
_CLEAN_JOB_IDS = {188, 171, 710, 99, 950, 6, 47, 128, 13, 1581, 381}


def build_dataset() -> None:
    client = Client()

    if not client.has_dataset(dataset_name=DATASET_NAME):
        client.create_dataset(
            dataset_name=DATASET_NAME,
            description=(
                "expertise_match rubric eval: same 20 real JDs as test_seniority, "
                "re-labeled for domain (D1-D3) x capability (C1-C6) x weakness "
                "(W1-W3) match, including the recruiter-detection rule (1a/1b) "
                "and the classical-vs-deep-learning ambiguity rule (4). Ground "
                "truth is human-labeled."
            ),
        )

    session = get_session()
    try:
        examples = []
        for job_id, score, category in EXAMPLES:
            job = session.get(Job, job_id)
            examples.append({
                "inputs": {"job_id": job_id, "posting": get_job_posting(job_id)},
                "outputs": {"score": score},
                "metadata": {"title": job.title, "company_name": job.company_name, "category": category},
                "split": "clean" if job_id in _CLEAN_JOB_IDS else "ambiguous",
            })
    finally:
        session.close()

    client.create_examples(dataset_name=DATASET_NAME, examples=examples)
    print(f"Uploaded {len(examples)} examples to dataset '{DATASET_NAME}'.")


if __name__ == "__main__":
    build_dataset()
