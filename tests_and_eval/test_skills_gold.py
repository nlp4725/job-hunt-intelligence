"""Gate: the skill pipeline must not get worse on the human-verified gold set.

Unit tests (test_skill_extraction.py) pin individual bugs; this measures the
pipeline as a whole — precision and recall for JDs and resumes, and how far
Skill Match scores computed from extracted skills drift from scores computed
from gold skills. Baseline lives in fixtures/skills_gold/baseline.json; refresh
it deliberately with `python -m tests_and_eval.skills_gold_eval --update-baseline`
after an intended change.
"""

import json

import pytest

from tests_and_eval.skills_gold_eval import BASELINE, evaluate, load_docs, score_mae

TOLERANCE = 0.02
MIN_DOCS = {"jd": 20, "resume": 3}


@pytest.fixture(scope="module")
def results():
    if not BASELINE.exists():
        pytest.skip("no baseline yet: verify labels, then run skills_gold_eval --update-baseline")
    docs = load_docs()
    return json.loads(BASELINE.read_text()), evaluate(docs), score_mae(docs)


def _errors(report: dict, kind: str) -> str:
    return "\n".join(f"  {doc} {kind_} {skill}" for doc, kind_, skill in report[kind]["errors"][:40])


@pytest.mark.parametrize("kind", ["jd", "resume"])
@pytest.mark.parametrize("metric", ["precision", "recall"])
def test_metric_not_below_baseline(results, kind, metric):
    baseline, report, _ = results
    if report[kind]["docs"] < MIN_DOCS[kind]:
        pytest.skip(f"only {report[kind]['docs']} verified {kind} documents (need {MIN_DOCS[kind]})")
    current, floor = report[kind][metric], baseline[kind][metric] - TOLERANCE
    assert current is not None and current >= floor, (
        f"{kind} {metric} {current:.3f} < baseline {baseline[kind][metric]:.3f} - {TOLERANCE}\n"
        f"errors:\n{_errors(report, kind)}"
    )


def test_skill_match_score_error_not_above_baseline(results):
    baseline, _, mae = results
    if mae is None or baseline.get("score_mae") is None:
        pytest.skip("need verified JDs and resumes")
    assert mae <= baseline["score_mae"] + 0.1, f"score MAE {mae:.3f} > baseline {baseline['score_mae']:.3f} + 0.1"
