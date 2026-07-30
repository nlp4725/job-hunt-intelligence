"""
Top-500 US tech company matching — checks a scraped company_name against
data/top_us_tech_companies.json (497 US-headquartered entries pulled from
companiesmarketcap.com's global tech ranking, see that file for provenance).

Matching is name-normalized (lowercased, legal suffixes like Inc/LLC/Corp
stripped, punctuation removed) rather than exact-string, since scraped
company_name values rarely match the market-cap list's formatting exactly
(e.g. "Akamai Technologies" vs "Akamai"). Entries with a parenthetical alias
(e.g. "Alphabet (Google)", "Meta Platforms (Facebook)") register both names
as candidates, so a scraped "Google" or "Meta Platforms" both match.
"""

import json
import re
from pathlib import Path

DATA_PATH = Path(__file__).parent.parent / "data" / "top_us_tech_companies.json"

_SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|llc|ltd|co|company|holdings?|group|technologies|technology)\b\.?",
    re.IGNORECASE,
)


def _normalize(name: str | None) -> str:
    if not name:
        return ""
    name = name.lower().strip()
    name = re.sub(r"&amp;", "&", name)
    name = _SUFFIXES.sub("", name)
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _load_candidates() -> set[str]:
    with open(DATA_PATH) as f:
        top500 = json.load(f)

    candidates = set()
    for name in top500:
        m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", name)
        parts = [name] if not m else [m.group(1), m.group(2)]
        for p in parts:
            norm = _normalize(p)
            if norm:
                candidates.add(norm)
    return candidates


_CANDIDATES = _load_candidates()  # loaded once at import time


def is_top500_tech(company_name: str | None) -> bool:
    return _normalize(company_name) in _CANDIDATES
