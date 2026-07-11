"""
Ad-hoc check of a LangChain-orchestrated version of the company research
call: ChatOpenAI (gpt-5.1) as the LLM, Perplexity's raw Search API
($5/1,000 requests — NOT the Agent API) as a client-side tool the model
calls itself, in a manual tool_use/tool_result loop. This is the
architecture discussed as an alternative to test_perplexity_raw.py's
single-call Agent API approach: we orchestrate the loop instead of letting
Perplexity do it server-side, in exchange for LangChain's native
LangSmith tracing (tool calls + token usage show up automatically, no
manual @traceable/usage_metadata plumbing like the other script needs).

Tool cost is the one thing LangChain's automatic tracing can't know about:
Perplexity's raw Search API has no per-token cost, just a flat
$0.005/request fee with no cost field in the response at all — that's
computed and logged by hand here, same as the tool-fee gap discussed for
the Agent API script.

Requires OPENAI_API_KEY in the environment (not needed by the other
scripts, which route through Perplexity rather than calling OpenAI
directly) in addition to PERPLEXITY_API_KEY and the LANGSMITH_* vars.
"""

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langsmith import get_current_run_tree, traceable
from perplexity import Perplexity
from pydantic import BaseModel, Field

MODEL = "gpt-5.1"
SEARCH_COST_PER_CALL_USD = 0.005  # Perplexity raw Search API: $5 per 1,000 requests

COMPANY_NAME = "Hightouch"
COMPANY_SIZE = "100-500 employees"

INSTRUCTIONS = """You are helping a job candidate research a company before applying. \
Generate a report less than 200 words. Research angles are employee sentiment (Glassdoor, \
Reddit, Blind), company stability (funding stage, revenue/ARR, layoffs), compensation \
level (levels.fyi), and momentum (recent funding, product traction, hiring velocity). \
Bold concrete figures (ratings, ARR, valuation) in your prose. If you can't find data \
for a field, say so plainly rather than guessing. Use the search_web tool as needed, \
then write the final report."""

INPUT_TEMPLATE = "Research {company} with a {size} employees."

# Caps total search_web *invocations*, not rounds — a single round can
# request several tool calls in parallel (we've seen 4 requested at once),
# so a round-based cap doesn't actually bound total searches made.
_MAX_SEARCH_CALLS = 4

_client = Perplexity()


class CompanyReport(BaseModel):
    headline: str = Field(description="1-2 sentence opening summary: growth stage, headcount, overall profile.")
    stability_and_momentum: str = Field(
        description="Paragraph on funding stage, revenue/ARR, valuation, market position, and momentum. Bold key figures in markdown."
    )
    compensation: str = Field(
        description="1-2 sentences on compensation level per levels.fyi (or comparable), bolding concrete figures."
    )
    glassdoor_rating: str = Field(
        description="The overall Glassdoor rating out of 5 and review count, e.g. '4.6/5 (81 reviews)'. If no Glassdoor page exists for this company, say so plainly rather than guessing."
    )
    working_experience_pros: str = Field(description="1-3 sentences on positive Glassdoor/Reddit/Blind themes.")
    working_experience_cons: str = Field(description="1-3 sentences on negative Glassdoor/Reddit/Blind themes.")
    verdict: str = Field(description="One sentence: what kind of candidate this company suits.")


def _to_markdown(report: CompanyReport) -> str:
    return f"""{report.headline}

**Company Stability & Momentum**

{report.stability_and_momentum}

**Compensation**

{report.compensation}

**Glassdoor Rating:** {report.glassdoor_rating}

**Working Experience (Glassdoor/Reddit)**

- **Pros:** {report.working_experience_pros}
- **Cons:** {report.working_experience_cons}

**Verdict:** {report.verdict}"""


@tool(response_format="content_and_artifact")
def search_web(query: str) -> tuple[str, list[dict]]:
    """Search the web for a query and get back results (title, url, snippet, date)."""
    response = _client.search.create(query=query, max_results=5)
    sources = [
        {"title": r.title, "url": r.url, "date": r.date, "snippet": r.snippet} for r in response.results
    ]
    # content: what the model reads. artifact: what we keep for ourselves
    # (LangChain tool convention for "model needs the text, caller needs the
    # structured data" — mirrors why test_perplexity_raw.py pulls sources
    # out of the response separately from output_text).
    content = "\n\n".join(f"{s['title']} ({s['date']}) {s['url']}\n{s['snippet']}" for s in sources)
    return content, sources


@traceable(name="test_langchain_raw")
def test_langchain_raw(company: str, size: str) -> dict:
    llm = ChatOpenAI(model=MODEL)
    llm_with_tools = llm.bind_tools([search_web])

    messages = [
        SystemMessage(INSTRUCTIONS),
        HumanMessage(INPUT_TEMPLATE.format(company=company, size=size)),
    ]

    sources = []
    search_queries = []

    while len(search_queries) < _MAX_SEARCH_CALLS:
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for tool_call in response.tool_calls:
            if len(search_queries) >= _MAX_SEARCH_CALLS:
                # Every tool_call in the assistant's turn needs a matching
                # ToolMessage or the next API call errors — so calls past
                # the cap still get a response, just a refusal instead of
                # an actual search.
                messages.append(
                    ToolMessage(content="Search limit reached — no further searches available.", tool_call_id=tool_call["id"])
                )
                continue
            search_queries.append(tool_call["args"]["query"])
            tool_message = search_web.invoke(tool_call)
            sources.extend(tool_message.artifact)
            messages.append(tool_message)

    structured_llm = llm.with_structured_output(CompanyReport)
    messages.append(HumanMessage("Now write the final report."))
    report = structured_llm.invoke(messages)

    # Token usage and the tool_use/tool_result blocks above are traced
    # automatically by LangChain's native LangSmith integration — no manual
    # usage_metadata code needed here, unlike test_perplexity_raw.py.
    # Tool *cost* is the one gap: Perplexity's raw Search API has no
    # per-token cost and no cost field in its response at all (flat
    # $0.005/request), so nothing automatically knows the dollar cost of
    # the searches above — logged by hand since it's the only thing that
    # can't be inferred from tokens.
    run = get_current_run_tree()
    run.metadata["search_tool_calls"] = len(search_queries)
    run.metadata["search_queries"] = search_queries
    run.metadata["search_tool_cost_usd"] = len(search_queries) * SEARCH_COST_PER_CALL_USD

    return {"report": _to_markdown(report), "sources": sources}


if __name__ == "__main__":
    result = test_langchain_raw(COMPANY_NAME, COMPANY_SIZE)
    print(result["report"])
    print("\nSources:")
    for source in result["sources"]:
        print(f"- {source['title']} ({source['date']}) {source['url']}")
