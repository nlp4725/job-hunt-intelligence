"""
Ad-hoc check of the seniority_fit rubric on Claude Haiku 4.5, via LangChain
so the run is automatically traced to LangSmith (set LANGSMITH_TRACING=true
and LANGSMITH_API_KEY in .env to enable) — no manual usage_metadata plumbing
needed, unlike the raw-SDK test scripts elsewhere in this folder.

Companion to test_seniority_deepseek.py: same prompt (common.SENIORITY_PROMPT),
same job (common.JOB_ID), different model — run both and diff the output to
see where the two models disagree on years_required / score for the same JD.

No prompt caching here: Claude Haiku 4.5 requires a 4,096-token minimum
prefix before a cache_control breakpoint does anything (below that, Anthropic
silently serves it uncached rather than erroring) — SENIORITY_PROMPT alone is
~1,567 tokens, so a breakpoint on it would be a no-op at this prompt length.
"""

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from tests_and_eval.test_seniority.common import JOB_ID, SENIORITY_PROMPT, SeniorityFit, get_job_posting

MODEL = "claude-haiku-4-5-20251001"


@traceable(name="test_seniority_claude")
def test_seniority_claude(job_id: int = JOB_ID) -> dict:
    llm = ChatAnthropic(model=MODEL, temperature=0)
    structured_llm = llm.with_structured_output(SeniorityFit)

    response = structured_llm.invoke([
        SystemMessage(SENIORITY_PROMPT),
        HumanMessage(get_job_posting(job_id)),
    ])
    return response.model_dump()


if __name__ == "__main__":
    print(json.dumps(test_seniority_claude(), indent=2))
