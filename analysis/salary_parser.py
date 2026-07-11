"""
Deterministic parser for LinkedIn's `salary_text` format into numeric
annual salary_min/salary_max, e.g. "$180K/yr - $260K/yr" -> (180000.0, 260000.0).

All 371 distinct salary_text values seen in the DB as of 2026-07 follow
"$<num>K?/<yr|hr> - $<num>K?/<yr|hr>", with an optional trailing
" + Bonus, Stock options, ..." suffix that this parser ignores. Hourly
rates are normalized to an annual figure (assuming a 2,080-hour work
year) so salary_min/salary_max are always comparable across postings
regardless of how the original posting expressed pay.
"""

import re

_SALARY_RANGE = re.compile(
    r"\$([\d,]+(?:\.\d+)?)(K)?/(yr|hr)\s*-\s*\$([\d,]+(?:\.\d+)?)(K)?/(yr|hr)",
    re.IGNORECASE,
)

_HOURS_PER_YEAR = 2080


def _to_annual(amount: float, is_k: bool, unit: str) -> float:
    if is_k:
        amount *= 1000
    if unit.lower() == "hr":
        amount *= _HOURS_PER_YEAR
    return amount


def parse_salary_range(salary_text: str | None) -> tuple[float | None, float | None]:
    """Return (salary_min, salary_max) as annualized dollar amounts, or
    (None, None) if salary_text is missing or doesn't match the known format."""
    if not salary_text:
        return None, None

    match = _SALARY_RANGE.search(salary_text)
    if not match:
        return None, None

    lo_num, lo_k, lo_unit, hi_num, hi_k, hi_unit = match.groups()
    lo = _to_annual(float(lo_num.replace(",", "")), bool(lo_k), lo_unit)
    hi = _to_annual(float(hi_num.replace(",", "")), bool(hi_k), hi_unit)
    return lo, hi
