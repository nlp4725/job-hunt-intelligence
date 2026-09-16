"""Score judge/seniority_level.py against the confirmed seniority gold set.

    PYTHONPATH=. ./venv/bin/python -m tests_and_eval.seniority_gold_eval --split tune [--reps 3]
    PYTHONPATH=. ./venv/bin/python -m tests_and_eval.seniority_gold_eval --split test          # final, once
    PYTHONPATH=. ./venv/bin/python -m tests_and_eval.seniority_gold_eval --split test --details

Points are the entry-level score table (entry 5 … principal 0, not a fit 0,
unclear 3), the scale the original labels used. The target is ≥95% of runs
within one point. Makes real DeepSeek calls (~$0.002 each).

`--split test` prints only the totals unless `--details` is given: per-job
failures on the test set are what turn it into a second tuning set. Every run
is saved under fixtures/seniority_gold/runs/ with a hash of the prompt it used.
"""

import argparse
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from analysis.seniority_fit import proposed_scores, seniority_fit
from judge.seniority_level import SENIORITY_LEVEL_PROMPT, classify_job_seniority

GOLD = Path(__file__).resolve().parent / "fixtures" / "seniority_gold"
ENTRY = proposed_scores("entry")
TARGET_WITHIN_ONE = 0.95


def load_labels(split: str, status: str | None = "confirmed") -> list[dict]:
    labels = []
    for path in sorted((GOLD / "labels").glob("jd-*.json")):
        label = json.loads(path.read_text())
        if label["split"] == split and (status is None or label["status"] == status):
            labels.append(label)
    return labels


def points(level: str | None, non_fit_reason: str | None) -> int:
    return seniority_fit(level, non_fit_reason, ENTRY)


def prompt_version() -> str:
    return hashlib.sha256(SENIORITY_LEVEL_PROMPT.encode()).hexdigest()[:10]


def run(split: str, reps: int, workers: int = 8) -> dict:
    labels = load_labels(split)
    jobs = [(label, rep) for label in labels for rep in range(reps)]

    def one(item):
        label, rep = item
        posting = (GOLD / "jds" / f"jd-{label['job_id']}.txt").read_text()
        try:
            result = classify_job_seniority(posting)
            return label, rep, result.level, result.non_fit_reason, None
        except Exception as exc:   # a failed call counts as a miss, not a crash
            return label, rep, None, None, f"{type(exc).__name__}: {exc}"[:200]

    with ThreadPoolExecutor(workers) as pool:
        outcomes = list(pool.map(one, jobs))

    rows = []
    for label, rep, level, reason, error in outcomes:
        gold = label["gold"]
        expected, got = points(gold["level"], gold["non_fit_reason"]), points(level, reason)
        rows.append({
            "job_id": label["job_id"], "rep": rep, "gold_level": gold["level"], "gold_non_fit": gold["non_fit_reason"],
            "level": level, "non_fit": reason, "expected": expected, "got": got, "error": error,
            "exact": error is None and (level, reason) == (gold["level"], gold["non_fit_reason"]),
        })
    n = len(rows)
    summary = {
        "split": split, "prompt_version": prompt_version(), "jobs": len(labels), "runs": n,
        "within_one": sum(abs(r["expected"] - r["got"]) <= 1 for r in rows) / n if n else None,
        "mae": sum(abs(r["expected"] - r["got"]) for r in rows) / n if n else None,
        "exact": sum(r["exact"] for r in rows) / n if n else None,
        "failed_calls": sum(r["error"] is not None for r in rows),
        "non_fit_confusion": dict(Counter(f"{r['gold_non_fit']}->{r['non_fit']}" for r in rows
                                          if r["gold_non_fit"] != r["non_fit"])),
    }
    runs_dir = GOLD / "runs"
    runs_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (runs_dir / f"{stamp}-{split}-{summary['prompt_version']}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n")
    return {"summary": summary, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["tune", "test"], required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--details", action="store_true", help="per-job misses (always shown for tune)")
    args = parser.parse_args()

    unconfirmed = len(load_labels(args.split, status=None)) - len(load_labels(args.split))
    if unconfirmed:
        print(f"note: {unconfirmed} {args.split} labels not confirmed yet; they are left out")
    result = run(args.split, args.reps)
    s = result["summary"]
    if args.split == "tune" or args.details:
        by_job: dict[int, list] = {}
        for r in result["rows"]:
            by_job.setdefault(r["job_id"], []).append(r)
        for job_id, reps in sorted(by_job.items()):
            if any(abs(r["expected"] - r["got"]) > 1 or r["error"] for r in reps):
                gold = f"{reps[0]['gold_level']}/{reps[0]['gold_non_fit']} ({reps[0]['expected']})"
                got = ", ".join(r["error"] or f"{r['level']}/{r['non_fit']} ({r['got']})" for r in reps)
                print(f"MISS jd-{job_id}: gold {gold} · model {got}")
    verdict = "PASS" if s["within_one"] is not None and s["within_one"] >= TARGET_WITHIN_ONE else "FAIL"
    print(f"\n{s['split']} · prompt {s['prompt_version']} · {s['jobs']} jobs × {args.reps} = {s['runs']} runs · "
          f"within one {s['within_one']:.0%} · MAE {s['mae']:.3f} · exact {s['exact']:.0%} · "
          f"failed calls {s['failed_calls']} · non-fit mix-ups {s['non_fit_confusion']} · {verdict}")


if __name__ == "__main__":
    main()
