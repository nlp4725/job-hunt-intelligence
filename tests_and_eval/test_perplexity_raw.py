"""
Ad-hoc check of the raw Perplexity Agent API (`client.responses.create`),
with no Claude involved at all — just call it directly and print exactly
what comes back. Useful for seeing the real response shape and quality
before trusting any code built on top of it. Traced via LangSmith so
token/cost usage is visible — set LANGSMITH_TRACING=true and
LANGSMITH_API_KEY in the environment to enable; tracing is a no-op
without them.

Note: package name on PyPI is `perplexityai`, not `perplexity` — the
latter is an unrelated, broken, unofficial package that squats the same
`from perplexity import Perplexity` import name.
"""

import json

from langsmith import get_current_run_tree, traceable
from perplexity import Perplexity

MODEL = "openai/gpt-5.1"

COMPANY_NAME = "Hightouch"
COMPANY_SIZE = "100-500 employees"

# Stable across every call — the "what shape should this take" contract.
# response_format below is what actually enforces this; these instructions
# just tell the model what to put in each field.
INSTRUCTIONS = """You are helping a job candidate research a company before applying. \
Generate a report less than 200 words. Research angles are employee sentiment (Glassdoor, \
Reddit, Blind), company stability (funding stage, revenue/ARR, layoffs), compensation \
level (levels.fyi), and momentum (recent funding, product traction, hiring velocity). \
Bold concrete figures (ratings, ARR, valuation) in your prose. If you can't find data \
for a field, say so plainly rather than guessing."""

# Only the per-call variable changes here — company name and size bucket.
INPUT_TEMPLATE = "Research {company} with a {size} employees."

# Forces the same fields every run instead of leaving structure to chance.
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "1-2 sentence opening summary: growth stage, headcount, overall profile.",
        },
        "stability_and_momentum": {
            "type": "string",
            "description": "Paragraph on funding stage, revenue/ARR, valuation, market position, and momentum. Bold key figures in markdown.",
        },
        "compensation": {
            "type": "string",
            "description": "1-2 sentences on compensation level per levels.fyi (or comparable), bolding concrete figures.",
        },
        "glassdoor_rating": {
            "type": "string",
            "description": "The overall Glassdoor rating out of 5 and review count, e.g. '4.6/5 (81 reviews)'. If no Glassdoor page exists for this company, say so plainly rather than guessing.",
        },
        "working_experience_pros": {
            "type": "string",
            "description": "1-3 sentences on positive Glassdoor/Reddit/Blind themes.",
        },
        "working_experience_cons": {
            "type": "string",
            "description": "1-3 sentences on negative Glassdoor/Reddit/Blind themes.",
        },
        "verdict": {
            "type": "string",
            "description": "One sentence: what kind of candidate this company suits.",
        },
    },
    "required": [
        "headline",
        "stability_and_momentum",
        "compensation",
        "glassdoor_rating",
        "working_experience_pros",
        "working_experience_cons",
        "verdict",
    ],
    "additionalProperties": False,
}


def _to_markdown(report: dict) -> str:
    return f"""{report['headline']}

**Company Stability & Momentum**

{report['stability_and_momentum']}

**Compensation**

{report['compensation']}

**Glassdoor Rating:** {report['glassdoor_rating']}

**Working Experience (Glassdoor/Reddit)**

- **Pros:** {report['working_experience_pros']}
- **Cons:** {report['working_experience_cons']}

**Verdict:** {report['verdict']}"""


@traceable(
    name="test_perplexity_raw",
    run_type="llm",
    metadata={"ls_provider": "perplexity", "ls_model_name": MODEL},
)
def test_perplexity_raw(company: str, size: str) -> dict:
    client = Perplexity()
    response = client.responses.create(
        model=MODEL,
        instructions=INSTRUCTIONS,
        input=INPUT_TEMPLATE.format(company=company, size=size),
        tools=[
            {
                "type": "web_search",
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "company_report", "schema": REPORT_SCHEMA, "strict": True},
        },
    )

    # Plain @traceable doesn't know how to read token usage off an arbitrary
    # response shape (that's what wrap_anthropic does for the Claude variant,
    # and there's no Perplexity equivalent) — so it's reported by hand here.
    # LangSmith's own $ column is computed purely from token counts x a
    # pricing-table rate (needs an entry for ls_provider="perplexity" /
    # ls_model_name matching MODEL in workspace settings). That column will
    # always undercount here: when the model calls web_search, Perplexity
    # charges a flat $0.005/invocation on top of token cost, which has
    # nothing to do with token counts. Perplexity already computes the true
    # total server-side (usage.cost.total_cost, tokens + tool fees), so we
    # log that too as plain metadata — ground truth to check LangSmith's
    # token-based estimate against, not a replacement for it.
    usage = response.usage
    run = get_current_run_tree()
    run.set(
        usage_metadata={
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }
    )
    run.metadata["perplexity_reported_cost_usd"] = usage.cost.total_cost
    # invocation counts per tool (e.g. {"search_web": 1}) — how many times
    # the model actually called web_search this run, not just what it cost.
    run.metadata["tool_calls"] = {
        name: details.invocation for name, details in (usage.tool_calls_details or {}).items()
    }

    # response.output is a list of blocks, not just the final message — a
    # "search_results" block (the citations: title/url/snippet/date per
    # source, plus the actual query strings the model searched) shows up
    # alongside the "message" block with the synthesized text. output_text
    # alone drops the search_results block on the floor, so it's pulled out
    # here and returned/logged too, rather than silently discarding it.
    search_result_blocks = [block for block in response.output if block.type == "search_results"]
    sources = [{"title": r.title, "url": r.url, "date": r.date} for block in search_result_blocks for r in block.results]
    run.metadata["search_queries"] = [q for block in search_result_blocks for q in block.queries]

    # With response_format=json_schema, output_text is a JSON string
    # matching REPORT_SCHEMA rather than free-form prose.
    report = json.loads(response.output_text)

    return {"report": _to_markdown(report), "sources": sources}


if __name__ == "__main__":
    result = test_perplexity_raw(COMPANY_NAME, COMPANY_SIZE)
    print(result["report"])
    print("\nSources:")
    for source in result["sources"]:
        print(f"- {source['title']} ({source['date']}) {source['url']}")
