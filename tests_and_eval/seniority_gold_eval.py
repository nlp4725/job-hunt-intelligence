"""Score judge/seniority_level.py against the seniority gold set.

    PYTHONPATH=. ./venv/bin/python -m tests_and_eval.seniority_gold_eval --split tune [--reps 3]
    PYTHONPATH=. ./venv/bin/python -m tests_and_eval.seniority_gold_eval --split test --details     # final, once
    PYTHONPATH=. ./venv/bin/python -m tests_and_eval.seniority_gold_eval --split tune --drafts      # before review

A run is correct only when the level and is_contract both match the label
(decided 2026-09-16; agency postings are filtered out before this step); the target is ≥90% of runs. Each attribute's own
accuracy and within-one-point (entry-level score table) are reported alongside.
Makes real DeepSeek calls (~$0.002 each).

`--split test` prints only the totals unless `--details` is given: per-job
failures on the test set are what turn it into a second tuning set. Every run
is saved under fixtures/seniority_gold/runs/ with a hash of the prompt it used.
"""

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from analysis.seniority_fit import proposed_scores, seniority_fit
from judge.seniority_level import SENIORITY_LEVEL_PROMPT, classify_job_seniority

GOLD = Path(__file__).resolve().parent / "fixtures" / "seniority_gold"
ENTRY = proposed_scores("entry")
TARGET_ALL_CORRECT = 0.90


def load_labels(split: str, status: str | None = "confirmed") -> list[dict]:
    labels = []
    for path in sorted((GOLD / "labels").glob("jd-*.json")):
        label = json.loads(path.read_text())
        if label["split"] == split and (status is None or label["status"] == status):
            labels.append(label)
    return labels


def points(level: str | None, is_contract: bool) -> int:
    return seniority_fit(level, is_contract, ENTRY)


def prompt_version() -> str:
    return hashlib.sha256(SENIORITY_LEVEL_PROMPT.encode()).hexdigest()[:10]


def run(split: str, reps: int, workers: int = 8, drafts: bool = False) -> dict:
    """`drafts`: also score unconfirmed labels, using the draft as the label."""
    labels = load_labels(split, status=None if drafts else "confirmed")
    for label in labels:
        label["gold"] = label["gold"] or label["draft"]
    jobs = [(label, rep) for label in labels for rep in range(reps)]

    def one(item):
        label, rep = item
        posting = (GOLD / "jds" / f"jd-{label['job_id']}.txt").read_text()
        try:
            result = classify_job_seniority(posting)
            return label, rep, result.level, result.is_contract, None
        except Exception as exc:   # a failed call counts as a miss, not a crash
            return label, rep, None, False, f"{type(exc).__name__}: {exc}"[:200]

    with ThreadPoolExecutor(workers) as pool:
        outcomes = list(pool.map(one, jobs))

    rows = []
    for label, rep, level, contract, error in outcomes:
        gold = label["gold"]
        ok = error is None
        rows.append({
            "job_id": label["job_id"], "rep": rep, "label_status": label["status"],
            "gold_level": gold["level"], "gold_contract": gold["is_contract"],
            "level": level, "contract": contract, "error": error,
            "expected": points(gold["level"], gold["is_contract"]),
            "got": points(level, contract),
            "level_ok": ok and level == gold["level"],
            "contract_ok": ok and contract == gold["is_contract"],
        })
    for r in rows:
        r["all_ok"] = r["level_ok"] and r["contract_ok"]
    n = len(rows)

    def share(key):
        return sum(r[key] for r in rows) / n if n else None

    summary = {
        "split": split, "prompt_version": prompt_version(), "jobs": len(labels), "runs": n,
        "labels": "confirmed + drafts" if drafts else "confirmed",
        "all_correct": share("all_ok"), "level": share("level_ok"),
        "is_contract": share("contract_ok"),
        "within_one": sum(abs(r["expected"] - r["got"]) <= 1 for r in rows) / n if n else None,
        "failed_calls": sum(r["error"] is not None for r in rows),
    }
    runs_dir = GOLD / "runs"
    runs_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (runs_dir / f"{stamp}-{split}-{summary['prompt_version']}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n")
    return {"summary": summary, "rows": rows}


def _describe(level, contract) -> str:
    return f"{level or 'unclear'}/{'contract' if contract else '-'}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["tune", "test"], required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--details", action="store_true", help="per-job misses (always shown for tune)")
    parser.add_argument("--drafts", action="store_true", help="use draft labels where none is confirmed yet")
    args = parser.parse_args()

    unconfirmed = len(load_labels(args.split, status=None)) - len(load_labels(args.split))
    if unconfirmed:
        print(f"note: {unconfirmed} {args.split} labels not confirmed yet; "
              + ("their drafts are used" if args.drafts else "they are left out"))
    result = run(args.split, args.reps, drafts=args.drafts)
    s = result["summary"]
    if args.split == "tune" or args.details:
        by_job: dict[int, list] = {}
        for r in result["rows"]:
            by_job.setdefault(r["job_id"], []).append(r)
        for job_id, reps in sorted(by_job.items()):
            if not all(r["all_ok"] for r in reps):
                gold = _describe(reps[0]["gold_level"], reps[0]["gold_contract"])
                got = ", ".join(r["error"] or _describe(r["level"], r["contract"]) for r in reps)
                print(f"MISS jd-{job_id}: label {gold} · model {got}")
    verdict = "PASS" if s["all_correct"] is not None and s["all_correct"] >= TARGET_ALL_CORRECT else "FAIL"
    print(f"\n{s['split']} ({s['labels']}) · prompt {s['prompt_version']} · {s['jobs']} jobs × {args.reps} = {s['runs']} runs · "
          f"level and contract both correct {s['all_correct']:.0%} (target {TARGET_ALL_CORRECT:.0%}) · level {s['level']:.0%} · "
          f"is_contract {s['is_contract']:.0%} · within one {s['within_one']:.0%} · "
          f"failed calls {s['failed_calls']} · {verdict}")


if __name__ == "__main__":
    main()
