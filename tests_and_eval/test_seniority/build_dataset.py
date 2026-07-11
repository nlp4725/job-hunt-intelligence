"""
Builds/uploads the "test_seniority" LangSmith dataset: 20 real jobs from
data/job_hunt.db, hand-picked to span every score band plus the messy
rule-interaction cases surfaced while drafting common.SENIORITY_PROMPT
(range parsing, multi-number conflicts, title-vs-responsibility tension,
the agency-posting rule, no-years inference). Ground truth (`outputs.score`)
is human-labeled, confirmed one job at a time against the actual JD text —
not model-generated.

Re-running this script is safe: create_dataset() is a no-op if the dataset
already exists (checked via has_dataset first), and create_examples()
appends rather than duplicating on exact dataset_name + example match.
"""

from langsmith import Client

from db.models import Job
from db.session import get_session
from tests_and_eval.test_seniority.common import get_job_posting

DATASET_NAME = "test_seniority"

# (job_id, human-labeled score, category) — category is just a metadata tag
# for slicing results later (e.g. "how do the two models do on range-parsing
# specifically"), not used by any evaluator.
EXAMPLES = [
    (188, 4, "clean_anchor"),
    (171, 5, "clean_anchor"),
    (710, 3, "clean_anchor"),
    (99, 4, "range_parsing"),
    (337, 2, "range_parsing"),
    (950, 2, "clean_anchor"),
    (6, 3, "years_vs_responsibility_tension"),
    (47, 1, "years_vs_responsibility_tension"),
    (1032, 1, "years_vs_responsibility_tension"),
    (128, 1, "multi_number_conflict"),
    (67, 0, "multi_number_conflict"),
    (84, 1, "multi_number_conflict"),
    (141, 1, "no_years_inference"),
    (13, 0, "multi_number_conflict"),
    (122, 5, "title_vs_duty_mismatch"),
    (381, 0, "agency_rule"),
    (1581, 5, "no_years_inference"),
    (1617, 5, "range_parsing"),
    (34, 5, "title_vs_duty_mismatch"),
    (663, 0, "clean_anchor"),
]

# "clean" vs "ambiguous" split, per whether labeling required real judgment
# calls / back-and-forth vs. a single clean rule application.
_CLEAN_JOB_IDS = {188, 171, 710, 99, 950, 663, 337}


def build_dataset() -> None:
    client = Client()

    if not client.has_dataset(dataset_name=DATASET_NAME):
        client.create_dataset(
            dataset_name=DATASET_NAME,
            description=(
                "seniority_fit rubric eval: 20 real JDs spanning every score band "
                "(0-5) plus range-parsing, multi-number-conflict, title-vs-duty, "
                "and agency-posting edge cases. Ground truth is human-labeled."
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
