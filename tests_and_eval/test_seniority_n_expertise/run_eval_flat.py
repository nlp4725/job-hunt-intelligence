"""
Same combined-call eval as run_eval.py, but scored against CombinedFitFlat
(common.py) instead of the nested CombinedFit — every field at the top
level, prefixed by rubric, instead of grouped into seniority_fit/
expertise_match sub-objects.

Exists to isolate whether CombinedFit's ~10-13% parse-failure rate (the
model splitting one tool call into two, each missing the other's required
field — confirmed by inspecting the raw LangSmith trace) was a schema-shape
problem, fixable the same way judge_agent.py avoids it (one flat tool,
no nesting), or genuine cross-rubric interference that a schema change
can't fix.

Same models, same dataset, same repetitions as run_eval.py, so the two
experiments are directly comparable in LangSmith.
"""

import time

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langsmith import evaluate

from tests_and_eval.test_seniority_n_expertise.common import COMBINED_PROMPT, CombinedFitFlat

DATASET_NAME = "test_seniority_n_expertise"
NUM_REPETITIONS = 3

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
DEEPSEEK_MODEL = "deepseek-v4-pro"

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


def _flatten(parsed: CombinedFitFlat, cost_usd: float, latency_s: float) -> dict:
    return {
        "seniority_score": parsed.seniority_score,
        "expertise_score": parsed.expertise_score,
        "seniority_fit": {
            "evidence": parsed.seniority_evidence,
            "years_required": parsed.seniority_years_required,
            "inferred": parsed.seniority_inferred,
            "confidence": parsed.seniority_confidence,
            "note": parsed.seniority_note,
            "score": parsed.seniority_score,
        },
        "expertise_match": {
            "evidence": parsed.expertise_evidence,
            "matched_domains": parsed.expertise_matched_domains,
            "matched_capabilities": parsed.expertise_matched_capabilities,
            "matched_weaknesses": parsed.expertise_matched_weaknesses,
            "confidence": parsed.expertise_confidence,
            "note": parsed.expertise_note,
            "score": parsed.expertise_score,
        },
        "cost_usd": cost_usd,
        "latency_s": latency_s,
    }


def target_claude(inputs: dict) -> dict:
    llm = ChatAnthropic(model=CLAUDE_MODEL, temperature=0)
    structured_llm = llm.with_structured_output(CombinedFitFlat, include_raw=True)

    start = time.perf_counter()
    result = structured_llm.invoke([
        SystemMessage(COMBINED_PROMPT),
        HumanMessage(inputs["posting"]),
    ])
    latency_s = time.perf_counter() - start

    return _flatten(result["parsed"], _claude_cost_usd(result["raw"].usage_metadata), latency_s)


def target_deepseek_think_off(inputs: dict) -> dict:
    llm = ChatDeepSeek(model=DEEPSEEK_MODEL, extra_body={"thinking": {"type": "disabled"}})
    structured_llm = llm.with_structured_output(CombinedFitFlat, include_raw=True)

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
        experiment_prefix="claude-haiku-4-5-combined-flat",
    )


def run_deepseek_think_off_eval():
    return evaluate(
        target_deepseek_think_off,
        data=DATASET_NAME,
        evaluators=EVALUATORS,
        summary_evaluators=SUMMARY_EVALUATORS,
        num_repetitions=NUM_REPETITIONS,
        experiment_prefix="deepseek-think-off-combined-flat",
    )


if __name__ == "__main__":
    print("Running Claude Haiku 4.5 combined-flat experiment...")
    run_claude_eval()
    print("Running DeepSeek V4 Pro (thinking off) combined-flat experiment...")
    run_deepseek_think_off_eval()
