"""Parity gate for the job-level seniority prompt (productization plan §3.4).

Runs judge/seniority_level.py on the 20 hand-labelled jobs of the original
seniority eval (tests_and_eval/test_seniority/build_dataset.py) and scores
seniority_fit(level, non_fit_reason, "entry") against those labels. The labels
were written for an entry-level candidate, so the new prompt must match the
old rubric's error: mean absolute error 0.317 for DeepSeek V4 Pro.

Costs real DeepSeek calls (~20 jobs x reps, a few cents). Reads postings from
the local SQLite file, read-only.

    ./venv/bin/python -m tests_and_eval.seniority_level_parity.run_parity [--reps 3]
"""

import argparse
from concurrent.futures import ThreadPoolExecutor

from analysis.seniority_fit import seniority_fit
from judge.seniority_level import classify_job_seniority
from tests_and_eval.test_seniority.build_dataset import EXAMPLES
from tests_and_eval.test_seniority.common import get_job_posting

BASELINE_MAE = 0.317
NOISE = 0.1


def run_one(example, rep):
    job_id, label, category = example
    result = classify_job_seniority(get_job_posting(job_id))
    fit = seniority_fit(result.level, result.non_fit_reason, "entry")
    return job_id, label, category, rep, fit, result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=3)
    args = parser.parse_args()

    runs = [(example, rep) for example in EXAMPLES for rep in range(args.reps)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda r: run_one(*r), runs))

    errors = []
    for job_id, label, category, rep, fit, result in sorted(results, key=lambda r: (r[0], r[3])):
        errors.append(abs(fit - label))
        flag = "" if fit == label else "  <--"
        print(f"job {job_id:5d} {category:34s} label {label} fit {fit} "
              f"(level={result.level}, non_fit={result.non_fit_reason}, years={result.years_required}){flag}")

    mae = sum(errors) / len(errors)
    within_one = sum(e <= 1 for e in errors) / len(errors)
    verdict = "PASS" if mae <= BASELINE_MAE + NOISE else "FAIL"
    print(f"\n{len(errors)} runs · MAE {mae:.3f} (baseline {BASELINE_MAE}, allowed +{NOISE}) · within one {within_one:.0%} · {verdict}")


if __name__ == "__main__":
    main()
