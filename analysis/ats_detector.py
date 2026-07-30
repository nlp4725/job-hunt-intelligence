"""
ATS board detector — guesses whether a company posts jobs on Greenhouse or
Ashby by probing their public, unauthenticated job-board APIs directly
(boards-api.greenhouse.io, api.ashbyhq.com). No browser/Selenium needed:
these endpoints return plain JSON for any live board and 404 for anything
else, so a handful of slug guesses per company is enough to confirm a hit.

Slug guessing is heuristic (company display name -> likely URL slug) and
will miss companies whose ATS slug doesn't derive from their name (e.g.
rebrands, abbreviations) — those need manual lookup. Run standalone:
    python analysis/ats_detector.py
"""

import re
import time

import requests

_SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|llc|ltd|co|company|holdings?|group)\b\.?",
    re.IGNORECASE,
)

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

REQUEST_TIMEOUT = 8
DELAY_BETWEEN_REQUESTS = 0.3  # be polite, avoid tripping rate limits


def _normalize_words(name: str) -> list[str]:
    name = name.lower().strip()
    name = re.sub(r"&amp;", "and", name)
    name = re.sub(r"&", "and", name)
    name = _SUFFIXES.sub("", name)
    name = re.sub(r"[^\w\s]", " ", name)
    return [w for w in name.split() if w]


def slug_candidates(company_name: str) -> list[str]:
    """Ordered, deduped list of plausible ATS slugs for a company name."""
    words = _normalize_words(company_name)
    if not words:
        return []

    candidates = [
        "".join(words),           # "chatgptjobs"
        "-".join(words),          # "chatgpt-jobs"
    ]
    if len(words) > 1:
        candidates.append(words[0])  # "reddit" from "Reddit Inc"

    seen = set()
    ordered = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def check_greenhouse(slug: str) -> bool:
    try:
        resp = requests.get(GREENHOUSE_URL.format(slug=slug), timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        return False
    return resp.status_code == 200 and '"jobs"' in resp.text[:50]


def check_ashby(slug: str) -> bool:
    try:
        resp = requests.get(ASHBY_URL.format(slug=slug), timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        return False
    return resp.status_code == 200


def detect(company_name: str) -> dict:
    """Returns {"greenhouse": slug_or_None, "ashby": slug_or_None} for a company."""
    result = {"greenhouse": None, "ashby": None}
    for slug in slug_candidates(company_name):
        if result["greenhouse"] is None and check_greenhouse(slug):
            result["greenhouse"] = slug
        time.sleep(DELAY_BETWEEN_REQUESTS)
        if result["ashby"] is None and check_ashby(slug):
            result["ashby"] = slug
        time.sleep(DELAY_BETWEEN_REQUESTS)
        if result["greenhouse"] and result["ashby"]:
            break
    return result


if __name__ == "__main__":
    import csv
    from pathlib import Path

    csv_path = Path(__file__).parent.parent / "data" / "companies_remote_candidates.csv"
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))

    top20 = rows[:20]
    print(f"{'Company':<25} {'Jobs':>5}  {'Greenhouse':<20} {'Ashby':<20}")
    for row in top20:
        name = row["company_name"]
        found = detect(name)
        gh = found["greenhouse"] or "-"
        ab = found["ashby"] or "-"
        print(f"{name:<25} {row['job_count']:>5}  {gh:<20} {ab:<20}")
