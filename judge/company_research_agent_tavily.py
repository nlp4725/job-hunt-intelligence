"""
Company Research Agent (Tavily variant) — looks up a company's Glassdoor
rating and reviews using Tavily as a client tool: Claude requests a search,
we call Tavily ourselves, and hand the results back as a tool_result. This
is the "we do the searching" pattern, as opposed to
judge/company_research_agent.py's use of Anthropic's built-in web_search
server tool (where Anthropic does the searching internally, in one call).
"""

import os
from datetime import datetime, timezone

import anthropic
from langsmith import traceable
from langsmith.wrappers import wrap_anthropic
from tavily import TavilyClient

from db.models import Company

MODEL = "claude-opus-4-8"

_SEARCH_TOOL = {
    "name": "search_web",
    "description": "Search the web for a query and get back a list of results (title, url, short snippet).",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query to run."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = """You are researching a company for a job candidate deciding whether to apply. \
Use the search_web tool to find this company's Glassdoor rating and a few representative reviews \
(both positive and negative, if available). If you can't find a Glassdoor page for this company, say \
so plainly rather than guessing.

Report back the overall rating (out of 5, if found) and a short summary of what reviews say.
"""

# Bounds how many rounds of "Claude asks to search -> we search -> hand
# results back" we'll do before giving up and returning whatever text
# Claude has produced so far.
_MAX_TOOL_ROUNDS = 4


def _run_search(tavily_client: TavilyClient, query: str) -> list[dict]:
    response = tavily_client.search(query)
    return [
        {"title": r["title"], "url": r["url"], "content": r["content"]}
        for r in response.get("results", [])
    ]


@traceable(name="company_research_agent_tavily")
def get_or_create_report(
    company: Company, session,
    client: anthropic.Anthropic | None = None,
    tavily_client: TavilyClient | None = None,
) -> str:
    """Same contract as company_research_agent.get_or_create_report
    (cache-check, then generate and cache on Company.research_report), but
    sources search results from Tavily instead of Anthropic's built-in
    web_search."""
    if company.research_report:
        return company.research_report

    client = client or wrap_anthropic(anthropic.Anthropic())
    tavily_client = tavily_client or TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

    messages = [{"role": "user", "content": f"Find the Glassdoor rating and reviews for: {company.name}"}]
    response = None

    for _ in range(_MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=MODEL, max_tokens=1024, system=_SYSTEM_PROMPT,
            tools=[_SEARCH_TOOL], messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            break

        tool_results = [
            {
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": str(_run_search(tavily_client, tool_use.input["query"])),
            }
            for tool_use in tool_uses
        ]
        messages.append({"role": "user", "content": tool_results})

    report = "\n".join(block.text for block in response.content if block.type == "text").strip()

    company.research_report = report
    company.research_report_generated_at = datetime.now(timezone.utc)
    session.commit()
    return report
