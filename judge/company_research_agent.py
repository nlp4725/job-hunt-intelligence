"""
Company Research Agent — looks up a company's Glassdoor rating and reviews
via the Claude web_search tool and caches the result on
Company.research_report.
"""

from datetime import datetime, timezone

import anthropic
from langsmith import traceable
from langsmith.wrappers import wrap_anthropic

from db.models import Company

MODEL = "claude-opus-4-8"

_SYSTEM_PROMPT = """You are researching a company for a job candidate deciding whether to apply. \
Use web search to find this company's Glassdoor rating and a few representative reviews (both \
positive and negative, if available). If you can't find a Glassdoor page for this company, say so \
plainly rather than guessing.

Report back the overall rating (out of 5, if found) and a short summary of what reviews say.
"""


@traceable(name="company_research_agent")
def get_or_create_report(company: Company, session, client: anthropic.Anthropic | None = None) -> str:
    """Return the cached Glassdoor rating/review summary for `company`,
    generating it via web_search on a cache miss. Mutates and commits
    `company` in place.

    Traced via LangSmith (@traceable + wrap_anthropic) so token usage per
    call is visible for cost monitoring — set LANGSMITH_TRACING=true and
    LANGSMITH_API_KEY in the environment to enable; tracing is a no-op
    without them."""
    if company.research_report:
        return company.research_report

    client = client or wrap_anthropic(anthropic.Anthropic())
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": f"Find the Glassdoor rating and reviews for: {company.name}"}],
    )

    report = "\n".join(block.text for block in response.content if block.type == "text").strip()

    company.research_report = report
    company.research_report_generated_at = datetime.now(timezone.utc)
    session.commit()
    return report
