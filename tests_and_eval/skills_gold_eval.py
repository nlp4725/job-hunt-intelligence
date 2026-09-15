"""Precision / recall of the deterministic skill pipeline against the verified gold set.

    JD:      extract_skills(normalize(text))
    Resume:  extract_skills(resume_to_text(file).text)

Also reports Skill Match score error: every verified resume x JD pair scored from
gold skills vs from extracted skills (mean absolute error on the 0-5 score).

    ./venv/bin/python -m tests_and_eval.skills_gold_eval
    ./venv/bin/python -m tests_and_eval.skills_gold_eval --update-baseline
"""

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

from analysis.skill_match import skill_match_from_skills
from analysis.skills_extractor import extract_skills
from analysis.text_normalize import normalize
from resume.resume_text import resume_to_text

GOLD = Path(__file__).resolve().parent / "fixtures" / "skills_gold"
BASELINE = GOLD / "baseline.json"
KINDS = ("jd", "resume")


def load_docs(status: str | None = "verified") -> list[dict]:
    docs = []
    for sub in ("jds", "resumes"):
        for path in sorted((GOLD / sub).glob("*.json")):
            doc = json.loads(path.read_text())
            if status is None or doc["status"] == status:
                doc["_path"] = path.parent / doc["text_file"]
                docs.append(doc)
    return docs


def extract(doc: dict) -> set[str]:
    path = doc["_path"]
    if doc["kind"] == "jd":
        return set(extract_skills(normalize(path.read_text())))
    return set(extract_skills(resume_to_text(path.name, path.read_bytes()).text))


def evaluate(docs: list[dict]) -> dict:
    report = {}
    for kind in KINDS:
        tp = fp = fn = 0
        per_skill = {"false_positive": Counter(), "false_negative": Counter()}
        errors = []
        subset = [d for d in docs if d["kind"] == kind]
        for doc in subset:
            gold, found = set(doc["gold"]), extract(doc)
            tp += len(gold & found)
            for skill in sorted(found - gold):
                fp += 1
                per_skill["false_positive"][skill] += 1
                errors.append((doc["id"], "false_positive", skill))
            for skill in sorted(gold - found):
                fn += 1
                per_skill["false_negative"][skill] += 1
                errors.append((doc["id"], "false_negative", skill))
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
        report[kind] = {
            "docs": len(subset), "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1,
            "per_skill": {k: dict(v.most_common()) for k, v in per_skill.items()},
            "errors": errors,
        }
    return report


def score_mae(docs: list[dict]) -> float | None:
    jds = [(set(d["gold"]), extract(d)) for d in docs if d["kind"] == "jd"]
    resumes = [(set(d["gold"]), extract(d)) for d in docs if d["kind"] == "resume"]
    diffs = [
        abs(skill_match_from_skills(jd_gold, res_gold)["score"] - skill_match_from_skills(jd_found, res_found)["score"])
        for jd_gold, jd_found in jds for res_gold, res_found in resumes
    ]
    return sum(diffs) / len(diffs) if diffs else None


def _fmt(x):
    return "—" if x is None else f"{x:.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    docs = load_docs()
    pending = len(load_docs(status=None)) - len(docs)
    report, mae = evaluate(docs), score_mae(docs)
    print(f"verified documents: {len(docs)} ({pending} still draft)")
    for kind in KINDS:
        r = report[kind]
        print(f"\n{kind.upper()}: {r['docs']} docs · precision {_fmt(r['precision'])} · recall {_fmt(r['recall'])} · F1 {_fmt(r['f1'])}")
        print(f"  false positives by skill: {r['per_skill']['false_positive']}")
        print(f"  false negatives by skill: {r['per_skill']['false_negative']}")
    print(f"\nSkill Match score MAE (gold vs extracted, resume x JD pairs): {_fmt(mae)}")

    if args.update_baseline:
        BASELINE.write_text(json.dumps({
            "date": date.today().isoformat(),
            **{k: {"docs": report[k]["docs"], "precision": report[k]["precision"], "recall": report[k]["recall"]} for k in KINDS},
            "score_mae": mae,
        }, indent=1) + "\n")
        print(f"\nwrote {BASELINE}")


if __name__ == "__main__":
    main()
