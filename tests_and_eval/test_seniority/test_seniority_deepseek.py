"""
Selected production config for seniority_fit: DeepSeek V4 Pro, thinking
mode disabled. Chosen over both Claude Haiku 4.5 and thinking-on DeepSeek
based on tests_and_eval/test_seniority/run_eval.py's 3-way eval — this
config was cheapest, fastest, AND most accurate (MAE 0.317 vs Claude's
0.333 and thinking-on DeepSeek's 0.517). See CONTEXT.md ("Stage 1 model
choice") for the full comparison.

Model name note: DeepSeek's legacy "deepseek-chat"/"deepseek-reasoner"
identifiers retire July 24, 2026 in favor of "deepseek-v4-pro" — this uses
the new name.

Thinking-mode note: deepseek-v4-pro defaults to thinking mode on, which
rejects forced tool_choice (400 "Thinking mode does not support this
tool_choice") — disabled here via extra_body={"thinking": {"type":
"disabled"}}, which also means the default with_structured_output()
method (schema-enforced via forced tool call) works normally, rather than
needing json_mode's prompt-only enforcement.

Cost tracking: LangSmith's automatic $ column only works if the workspace's
model-pricing table has a rate entry for the provider/model — there's a
built-in one for Claude, but none for deepseek-v4-pro (that's a LangSmith
workspace-settings addition, not something set from code). Token counts are
still traced automatically by LangChain either way; cost is computed here
by hand from those counts, same gap-filling pattern test_perplexity_raw.py
uses for Perplexity's tool-call fees. Rates are DeepSeek's published
per-1M-token prices (api-docs.deepseek.com/quick_start/pricing) as of this
writing — check there before trusting this number long after the fact,
DeepSeek's pricing has moved before.
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langsmith import get_current_run_tree, traceable

from tests_and_eval.test_seniority.common import JOB_ID, SENIORITY_PROMPT, SeniorityFit, get_job_posting

MODEL = "deepseek-v4-pro"

# USD per 1M tokens.
PRICE_PER_M_FRESH_INPUT = 0.435
PRICE_PER_M_CACHED_INPUT = 0.003625
PRICE_PER_M_OUTPUT = 0.87


def _cost_usd(usage_metadata: dict) -> float:
    cached = (usage_metadata.get("input_token_details") or {}).get("cache_read", 0) or 0
    fresh = usage_metadata["input_tokens"] - cached
    return (
        fresh / 1e6 * PRICE_PER_M_FRESH_INPUT
        + cached / 1e6 * PRICE_PER_M_CACHED_INPUT
        + usage_metadata["output_tokens"] / 1e6 * PRICE_PER_M_OUTPUT
    )


@traceable(name="test_seniority_deepseek")
def test_seniority_deepseek(job_id: int = JOB_ID) -> dict:
    llm = ChatDeepSeek(model=MODEL, extra_body={"thinking": {"type": "disabled"}})
    structured_llm = llm.with_structured_output(SeniorityFit, include_raw=True)

    result = structured_llm.invoke([
        SystemMessage(SENIORITY_PROMPT),
        HumanMessage(get_job_posting(job_id)),
    ])

    usage = result["raw"].usage_metadata
    run = get_current_run_tree()
    run.set(usage_metadata=usage)
    run.metadata["deepseek_computed_cost_usd"] = _cost_usd(usage)

    return result["parsed"].model_dump()


if __name__ == "__main__":
    print(json.dumps(test_seniority_deepseek(), indent=2))
