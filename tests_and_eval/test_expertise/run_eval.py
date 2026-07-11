"""
Runs the expertise_match rubric against the "test_expertise" dataset
(build_dataset.py) for Claude Haiku 4.5 and DeepSeek V4 Pro (thinking
disabled), 3 repetitions each, as two separate LangSmith experiments
against the same dataset. Ground truth is the human-labeled
`outputs.score` on each example.

Only thinking-off is run for DeepSeek here — the test_seniority eval
found thinking-off was faster, cheaper, AND more accurate than
thinking-on for that rubric (run_eval.py's 3-model comparison), so
there's no reason to re-litigate thinking-on for this rubric too.

Cost and latency are computed inside the target functions themselves
rather than read off LangSmith's automatic tracking, same reasoning as
tests_and_eval/test_seniority/run_eval.py: LangSmith's native cost
column has no pricing-table entry for deepseek-v4-pro, so computing both
models' cost the same way keeps the comparison apples-to-apples.
"""

import time

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langsmith import evaluate

from tests_and_eval.test_expertise.common import EXPERTISE_MATCH_PROMPT, ExpertiseMatch

DATASET_NAME = "test_expertise"
NUM_REPETITIONS = 3

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
DEEPSEEK_MODEL = "deepseek-v4-pro"

# USD per 1M tokens (see tests_and_eval/test_seniority/run_eval.py for the
# source and caveats — point-in-time published rates, not fetched live).
CLAUDE_PRICE_PER_M_INPUT = 1.00
CLAUDE_PRICE_PER_M_OUTPUT = 5.00
DEEPSEEK_PRICE_PER_M_FRESH_INPUT = 0.435
DEEPSEEK_PRICE_PER_M_CACHED_INPUT = 0.003625
DEEPSEEK_PRICE_PER_M_OUTPUT = 0.87


def _claude_cost_usd(usage: dict) -> float:
    cached = (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
    fresh = usage["input_tokens"] - cached
    return fresh / 1e6 * CLAUDE_PRICE_PER_M_INPUT + usage["output_tokens"] / 1e6 * CLAUDE_PRICE_PER_M_OUTPUT


def _deepseek_cost_usd(usage: dict) -> float:
    cached = (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
    fresh = usage["input_tokens"] - cached
    return (
        fresh / 1e6 * DEEPSEEK_PRICE_PER_M_FRESH_INPUT
        + cached / 1e6 * DEEPSEEK_PRICE_PER_M_CACHED_INPUT
        + usage["output_tokens"] / 1e6 * DEEPSEEK_PRICE_PER_M_OUTPUT
    )


def target_claude(inputs: dict) -> dict:
    llm = ChatAnthropic(model=CLAUDE_MODEL, temperature=0)
    structured_llm = llm.with_structured_output(ExpertiseMatch, include_raw=True)

    start = time.perf_counter()
    result = structured_llm.invoke([
        SystemMessage(EXPERTISE_MATCH_PROMPT),
        HumanMessage(inputs["posting"]),
    ])
    latency_s = time.perf_counter() - start

    return {
        **result["parsed"].model_dump(),
        "cost_usd": _claude_cost_usd(result["raw"].usage_metadata),
        "latency_s": latency_s,
    }


def target_deepseek_think_off(inputs: dict) -> dict:
    llm = ChatDeepSeek(model=DEEPSEEK_MODEL, extra_body={"thinking": {"type": "disabled"}})
    structured_llm = llm.with_structured_output(ExpertiseMatch, include_raw=True)

    start = time.perf_counter()
    result = structured_llm.invoke([
        SystemMessage(EXPERTISE_MATCH_PROMPT),
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
    print("Running DeepSeek V4 Pro (thinking off) experiment...")
    run_deepseek_think_off_eval()
