"""
Runs the seniority_fit rubric against the "test_seniority" dataset
(build_dataset.py) for both Claude Haiku 4.5 and DeepSeek V4 Pro, 3
repetitions each, as two separate LangSmith experiments against the same
dataset. Ground truth is the human-labeled `outputs.score` on each example.

Cost and latency are computed inside the target functions themselves rather
than read off LangSmith's automatic tracking, because that only covers
Claude (there's no workspace pricing-table entry for deepseek-v4-pro, per
test_seniority_deepseek.py) — computing both the same way here keeps the
cost comparison apples-to-apples instead of "free for one model, hand-rolled
for the other."

Pairwise comparison: intentionally not using evaluate_comparative() — that
API is for preference judging when there's no ground truth (an LLM picks
"A or B is better"). We have real ground truth here, so the two experiments
below can just be opened side by side in LangSmith's built-in comparison
view once both have run.
"""

import time

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langsmith import evaluate

from tests_and_eval.test_seniority.common import SENIORITY_PROMPT, SeniorityFit
from tests_and_eval.test_seniority.test_seniority_deepseek import _cost_usd as _deepseek_cost_usd

DATASET_NAME = "test_seniority"
NUM_REPETITIONS = 3

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
DEEPSEEK_MODEL = "deepseek-v4-pro"

# USD per 1M tokens (see test_seniority_claude.py / test_seniority_deepseek.py
# for the source and caveats — these are point-in-time published rates, not
# fetched live).
CLAUDE_PRICE_PER_M_INPUT = 1.00
CLAUDE_PRICE_PER_M_OUTPUT = 5.00


def _claude_cost_usd(usage: dict) -> float:
    cached = (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
    fresh = usage["input_tokens"] - cached
    return fresh / 1e6 * CLAUDE_PRICE_PER_M_INPUT + usage["output_tokens"] / 1e6 * CLAUDE_PRICE_PER_M_OUTPUT


def target_claude(inputs: dict) -> dict:
    llm = ChatAnthropic(model=CLAUDE_MODEL, temperature=0)
    structured_llm = llm.with_structured_output(SeniorityFit, include_raw=True)

    start = time.perf_counter()
    result = structured_llm.invoke([
        SystemMessage(SENIORITY_PROMPT),
        HumanMessage(inputs["posting"]),
    ])
    latency_s = time.perf_counter() - start

    return {
        **result["parsed"].model_dump(),
        "cost_usd": _claude_cost_usd(result["raw"].usage_metadata),
        "latency_s": latency_s,
    }


def target_deepseek(inputs: dict) -> dict:
    llm = ChatDeepSeek(model=DEEPSEEK_MODEL)
    structured_llm = llm.with_structured_output(SeniorityFit, method="json_mode", include_raw=True)

    start = time.perf_counter()
    result = structured_llm.invoke([
        SystemMessage(SENIORITY_PROMPT),
        HumanMessage(inputs["posting"]),
    ])
    latency_s = time.perf_counter() - start

    return {
        **result["parsed"].model_dump(),
        "cost_usd": _deepseek_cost_usd(result["raw"].usage_metadata),
        "latency_s": latency_s,
    }


def target_deepseek_think_off(inputs: dict) -> dict:
    """Same model, thinking mode explicitly disabled — tests whether the
    reasoning trace was actually load-bearing for this rubric's harder cases
    (multi-number conflicts, degree-tiered years) or just overhead. With
    thinking off, DeepSeek no longer rejects forced tool_choice, so this can
    use the more robust default structured-output method (schema-enforced by
    the API) instead of json_mode's prompt-only enforcement."""
    llm = ChatDeepSeek(model=DEEPSEEK_MODEL, extra_body={"thinking": {"type": "disabled"}})
    structured_llm = llm.with_structured_output(SeniorityFit, include_raw=True)

    start = time.perf_counter()
    result = structured_llm.invoke([
        SystemMessage(SENIORITY_PROMPT),
        HumanMessage(inputs["posting"]),
    ])
    latency_s = time.perf_counter() - start

    return {
        **result["parsed"].model_dump(),
        "cost_usd": _deepseek_cost_usd(result["raw"].usage_metadata),
        "latency_s": latency_s,
    }


def absolute_error(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "absolute_error", "score": abs(outputs["score"] - reference_outputs["score"])}


def percent_error(outputs: dict, reference_outputs: dict) -> dict:
    # Normalized against the fixed 0-5 scale, not the reference value itself —
    # several examples have reference score 0, where error/reference is
    # undefined.
    error = abs(outputs["score"] - reference_outputs["score"]) / 5 * 100
    return {"key": "percent_error", "score": error}


def cost_usd_metric(outputs: dict) -> dict:
    return {"key": "cost_usd", "score": outputs["cost_usd"]}


def latency_s_metric(outputs: dict) -> dict:
    return {"key": "latency_s", "score": outputs["latency_s"]}


def summary_mae(outputs: list, reference_outputs: list) -> dict:
    errors = [abs(o["score"] - r["score"]) for o, r in zip(outputs, reference_outputs)]
    return {"key": "mae", "score": sum(errors) / len(errors)}


def summary_mean_percent_error(outputs: list, reference_outputs: list) -> dict:
    errors = [abs(o["score"] - r["score"]) / 5 * 100 for o, r in zip(outputs, reference_outputs)]
    return {"key": "mean_percent_error", "score": sum(errors) / len(errors)}


EVALUATORS = [absolute_error, percent_error, cost_usd_metric, latency_s_metric]
SUMMARY_EVALUATORS = [summary_mae, summary_mean_percent_error]


def run_claude_eval():
    return evaluate(
        target_claude,
        data=DATASET_NAME,
        evaluators=EVALUATORS,
        summary_evaluators=SUMMARY_EVALUATORS,
        num_repetitions=NUM_REPETITIONS,
        experiment_prefix="claude-haiku-4-5",
    )


def run_deepseek_eval():
    return evaluate(
        target_deepseek,
        data=DATASET_NAME,
        evaluators=EVALUATORS,
        summary_evaluators=SUMMARY_EVALUATORS,
        num_repetitions=NUM_REPETITIONS,
        experiment_prefix="deepseek-v4-pro",
    )


def run_deepseek_think_off_eval():
    return evaluate(
        target_deepseek_think_off,
        data=DATASET_NAME,
        evaluators=EVALUATORS,
        summary_evaluators=SUMMARY_EVALUATORS,
        num_repetitions=NUM_REPETITIONS,
        experiment_prefix="deepseek-think-off",
    )


if __name__ == "__main__":
    print("Running Claude Haiku 4.5 experiment...")
    run_claude_eval()
    print("Running DeepSeek V4 Pro experiment...")
    run_deepseek_eval()
    print("Running DeepSeek V4 Pro (thinking off) experiment...")
    run_deepseek_think_off_eval()
