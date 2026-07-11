"""
Runs the COMBINED seniority_fit + expertise_match rubric (one LLM call scores
both) against the "test_seniority_n_expertise" dataset (build_dataset.py),
for Claude Haiku 4.5 and DeepSeek V4 Pro (thinking off) — the same two
production-config models used standalone in test_seniority/run_eval.py and
test_expertise/run_eval.py — 3 repetitions each, as two separate LangSmith
experiments against the same dataset.

Point of this eval: compare the resulting per-label MAE against each
rubric's OWN standalone MAE (from CONTEXT.md "Stage 1 model choice":
seniority_fit DeepSeek-off MAE 0.317 / Claude MAE 0.333; expertise_match
Claude MAE 0.600 / DeepSeek-off MAE 0.700) to see whether combining into one
call costs accuracy on either label versus scoring them in two separate
calls.

Cost/latency computed the same way as test_seniority/run_eval.py and
test_expertise/run_eval.py (in the target function, not LangSmith's native
cost column) so both models are compared on an apples-to-apples basis.
"""

import time

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langsmith import evaluate

from tests_and_eval.test_seniority_n_expertise.common import COMBINED_PROMPT, CombinedFit

DATASET_NAME = "test_seniority_n_expertise"
NUM_REPETITIONS = 3

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
DEEPSEEK_MODEL = "deepseek-v4-pro"

# USD per 1M tokens (see test_seniority/run_eval.py / test_expertise/run_eval.py
# for the source and caveats — point-in-time published rates, not fetched live).
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


def _flatten(parsed: CombinedFit, cost_usd: float, latency_s: float) -> dict:
    return {
        "seniority_score": parsed.seniority_fit.score,
        "expertise_score": parsed.expertise_match.score,
        "seniority_fit": parsed.seniority_fit.model_dump(),
        "expertise_match": parsed.expertise_match.model_dump(),
        "cost_usd": cost_usd,
        "latency_s": latency_s,
    }


def target_claude(inputs: dict) -> dict:
    llm = ChatAnthropic(model=CLAUDE_MODEL, temperature=0)
    structured_llm = llm.with_structured_output(CombinedFit, include_raw=True)

    start = time.perf_counter()
    result = structured_llm.invoke([
        SystemMessage(COMBINED_PROMPT),
        HumanMessage(inputs["posting"]),
    ])
    latency_s = time.perf_counter() - start

    return _flatten(result["parsed"], _claude_cost_usd(result["raw"].usage_metadata), latency_s)


def target_deepseek_think_off(inputs: dict) -> dict:
    llm = ChatDeepSeek(model=DEEPSEEK_MODEL, extra_body={"thinking": {"type": "disabled"}})
    structured_llm = llm.with_structured_output(CombinedFit, include_raw=True)

    start = time.perf_counter()
    result = structured_llm.invoke([
        SystemMessage(COMBINED_PROMPT),
        HumanMessage(inputs["posting"]),
    ])
    latency_s = time.perf_counter() - start

    return _flatten(result["parsed"], _deepseek_cost_usd(result["raw"].usage_metadata), latency_s)


def seniority_absolute_error(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "seniority_absolute_error", "score": abs(outputs["seniority_score"] - reference_outputs["seniority_score"])}


def expertise_absolute_error(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "expertise_absolute_error", "score": abs(outputs["expertise_score"] - reference_outputs["expertise_score"])}


def cost_usd_metric(outputs: dict) -> dict:
    return {"key": "cost_usd", "score": outputs["cost_usd"]}


def latency_s_metric(outputs: dict) -> dict:
    return {"key": "latency_s", "score": outputs["latency_s"]}


def summary_seniority_mae(outputs: list, reference_outputs: list) -> dict:
    errors = [abs(o["seniority_score"] - r["seniority_score"]) for o, r in zip(outputs, reference_outputs)]
    return {"key": "seniority_mae", "score": sum(errors) / len(errors)}


def summary_expertise_mae(outputs: list, reference_outputs: list) -> dict:
    errors = [abs(o["expertise_score"] - r["expertise_score"]) for o, r in zip(outputs, reference_outputs)]
    return {"key": "expertise_mae", "score": sum(errors) / len(errors)}


EVALUATORS = [seniority_absolute_error, expertise_absolute_error, cost_usd_metric, latency_s_metric]
SUMMARY_EVALUATORS = [summary_seniority_mae, summary_expertise_mae]


def run_claude_eval():
    return evaluate(
        target_claude,
        data=DATASET_NAME,
        evaluators=EVALUATORS,
        summary_evaluators=SUMMARY_EVALUATORS,
        num_repetitions=NUM_REPETITIONS,
        experiment_prefix="claude-haiku-4-5-combined",
    )


def run_deepseek_think_off_eval():
    return evaluate(
        target_deepseek_think_off,
        data=DATASET_NAME,
        evaluators=EVALUATORS,
        summary_evaluators=SUMMARY_EVALUATORS,
        num_repetitions=NUM_REPETITIONS,
        experiment_prefix="deepseek-think-off-combined",
    )


if __name__ == "__main__":
    print("Running Claude Haiku 4.5 combined experiment...")
    run_claude_eval()
    print("Running DeepSeek V4 Pro (thinking off) combined experiment...")
    run_deepseek_think_off_eval()
