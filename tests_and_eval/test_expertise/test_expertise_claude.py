"""
Ad-hoc check of the expertise_match rubric on Claude Haiku 4.5, via
LangChain so the run is automatically traced to LangSmith. Mirrors
tests_and_eval/test_seniority/test_seniority_claude.py.
"""

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from tests_and_eval.test_expertise.common import EXPERTISE_MATCH_PROMPT, ExpertiseMatch, JOB_ID, get_job_posting

MODEL = "claude-haiku-4-5-20251001"


@traceable(name="test_expertise_claude")
def test_expertise_claude(job_id: int = JOB_ID) -> dict:
    llm = ChatAnthropic(model=MODEL, temperature=0)
    structured_llm = llm.with_structured_output(ExpertiseMatch)

    response = structured_llm.invoke([
        SystemMessage(EXPERTISE_MATCH_PROMPT),
        HumanMessage(get_job_posting(job_id)),
    ])
    return response.model_dump()


if __name__ == "__main__":
    print(json.dumps(test_expertise_claude(), indent=2))
