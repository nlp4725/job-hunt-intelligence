"""
Ad-hoc check of the raw Tavily search API, with no Claude involved at all —
just call .search() directly and print exactly what comes back. Useful for
seeing the real response shape before trusting any code built on top of it.
"""

import os

from tavily import TavilyClient

QUERY = "Working at Google"


def test_tavily_raw(query: str) -> None:
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    response = client.search(query, include_domains=["glassdoor.com"])
    print(response)


if __name__ == "__main__":
    test_tavily_raw(QUERY)
