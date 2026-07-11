"""
Selected production config for expertise_match: DeepSeek V4 Pro,
thinking mode disabled — same choice as seniority_fit, for consistency
and because thinking-on was already ruled out there (faster, cheaper,
and more accurate as thinking-off). For expertise_match specifically,
Claude Haiku 4.5 came out somewhat more accurate in the eval (MAE 0.600
vs DeepSeek's 0.700 — see run_eval.py / CONTEXT.md "Stage 1 model
choice"), but DeepSeek's ~15x lower cost was judged worth the small
accuracy gap for a first-pass screen meant to run over the whole corpus.

With thinking off, DeepSeek no longer rejects forced tool_choice, so this
uses the default structured-output method (schema-enforced by the API)
instead of test_seniority_deepseek.py's earlier json_mode workaround.
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langsmith import traceable

from tests_and_eval.test_expertise.common import EXPERTISE_MATCH_PROMPT, ExpertiseMatch, JOB_ID, get_job_posting

MODEL = "deepseek-v4-pro"


@traceable(name="test_expertise_deepseek")
def test_expertise_deepseek(job_id: int = JOB_ID) -> dict:
    llm = ChatDeepSeek(model=MODEL, extra_body={"thinking": {"type": "disabled"}})
    structured_llm = llm.with_structured_output(ExpertiseMatch)

    response = structured_llm.invoke([
        SystemMessage(EXPERTISE_MATCH_PROMPT),
        HumanMessage(get_job_posting(job_id)),
    ])
    return response.model_dump()


if __name__ == "__main__":
    print(json.dumps(test_expertise_deepseek(), indent=2))
