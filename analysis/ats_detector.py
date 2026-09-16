"""
ATS board detector — guesses whether a company posts jobs on a
LinkedIn-independent ATS by probing each platform's public, unauthenticated
job-board API directly. No browser/Selenium needed: these endpoints return
plain JSON for any live board and 404 (or equivalent) for anything else, so
a handful of slug guesses per company is enough to confirm a hit.

Providers checked, in this order (roughly most-to-least common among tech
employers, so the average company short-circuits on an early check):
Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, Breezy,
Teamtailor. Workday/iCIMS/Taleo/SuccessFactors are deliberately excluded —
those don't have a slug-guessable public API (Workday needs a tenant name +
instance number + site name that can't be derived from the company's
display name alone; see the ats_detector conversation in CONTEXT.md).

Slug guessing is heuristic (company display name -> likely URL slug) and
will miss companies whose ATS slug doesn't derive from their name — those
need manual lookup. Results persist to companies.ats_provider/ats_slug/
ats_checked_at (db/models.py) so a re-run only needs to check companies
that haven't been probed yet.

Run standalone:
    python analysis/ats_detector.py                  # full sweep, all providers
    python analysis/ats_detector.py --provider=lever  # only check one provider
    python analysis/ats_detector.py --limit=50        # only the first N companies (by job_count desc)
"""

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

_SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|llc|ltd|co|company|holdings?|group)\b\.?",
    re.IGNORECASE,
)

REQUEST_TIMEOUT = 8
PER_REQUEST_DELAY = 0.1  # light politeness delay; real throttling comes from WORKERS count below
WORKERS = 10


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


def _get(url: str) -> requests.Response | None:
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        # Broad on purpose: a malformed slug can raise things that aren't
        # requests.RequestException at all (e.g. urllib3's LocationParseError
        # for a subdomain over DNS's 63-char label limit, seen in practice
        # on a long company name) — any failure to reach the server just
        # means "not a match," never worth crashing the whole sweep over.
        return None
    finally:
        time.sleep(PER_REQUEST_DELAY)
    return resp


MAX_DNS_LABEL_LENGTH = 63  # subdomain-based providers (recruitee/breezy/teamtailor) can't have a label longer than this regardless of whether the company is real


def _check_greenhouse(slug: str) -> bool:
    resp = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    return resp is not None and resp.status_code == 200 and '"jobs"' in resp.text[:50]


def _check_lever(slug: str) -> bool:
    resp = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    return resp is not None and resp.status_code == 200 and resp.text.strip().startswith("[")


def _check_ashby(slug: str) -> bool:
    resp = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return resp is not None and resp.status_code == 200


def _check_smartrecruiters(slug: str) -> bool:
    # Unlike every other provider here, this endpoint returns 200 with an
    # empty {"content":[]} for ANY slug — real or not (confirmed against a
    # deliberately nonsense slug) — so status/shape alone can't tell a valid
    # company apart from a typo. totalFound > 0 is the only real signal: it
    # also happens to be the bar we actually care about (an empty board is
    # useless to scrape regardless of whether the slug is genuine).
    resp = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings")
    if resp is None or resp.status_code != 200:
        return False
    try:
        return resp.json().get("totalFound", 0) > 0
    except ValueError:
        return False


def _check_workable(slug: str) -> bool:
    # 200 alone isn't enough — big companies with no real Workable presence
    # (Microsoft, Oracle) still return 200 with a matching "name" and an
    # empty jobs list, presumably a dormant/unused sub-account. Same fix as
    # SmartRecruiters: require actual postings.
    resp = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    if resp is None or resp.status_code != 200:
        return False
    try:
        return len(resp.json().get("jobs", [])) > 0
    except ValueError:
        return False


def _check_recruitee(slug: str) -> bool:
    if len(slug) > MAX_DNS_LABEL_LENGTH:
        return False
    resp = _get(f"https://{slug}.recruitee.com/api/offers/")
    return resp is not None and resp.status_code == 200 and '"offers"' in resp.text[:200]


def _check_breezy(slug: str) -> bool:
    # Same trap as SmartRecruiters/Workable — any subdomain resolves and
    # returns 200 "[]" rather than a real 404 (confirmed against
    # microsoft/oracle/walmart, none of which use Breezy). Require actual
    # postings.
    if len(slug) > MAX_DNS_LABEL_LENGTH:
        return False
    resp = _get(f"https://{slug}.breezy.hr/json")
    if resp is None or resp.status_code != 200:
        return False
    try:
        return len(resp.json()) > 0
    except ValueError:
        return False


def _check_teamtailor(slug: str) -> bool:
    if len(slug) > MAX_DNS_LABEL_LENGTH:
        return False
    resp = _get(f"https://{slug}.teamtailor.com/jobs.json")
    return resp is not None and resp.status_code == 200 and resp.text.strip().startswith(("[", "{"))


# Ordered roughly most-to-least common among tech employers, so a plain
# sweep over ALL_PROVIDERS short-circuits fast on average (see detect()).
ALL_PROVIDERS: list[tuple[str, "callable"]] = [
    ("greenhouse", _check_greenhouse),
    ("lever", _check_lever),
    ("ashby", _check_ashby),
    ("smartrecruiters", _check_smartrecruiters),
    ("workable", _check_workable),
    ("recruitee", _check_recruitee),
    ("breezy", _check_breezy),
    ("teamtailor", _check_teamtailor),
]


def detect(company_name: str, providers: list[tuple[str, "callable"]] | None = None) -> tuple[str | None, str | None]:
    """Returns (provider_name, slug) for the first matching provider/slug
    combination, or (None, None) if nothing matched. Tries each slug
    candidate against every provider in order, stopping at the first hit —
    a company is assumed to use one ATS, so this favors speed over
    exhaustively finding every possible match."""
    providers = providers or ALL_PROVIDERS
    for slug in slug_candidates(company_name):
        for provider_name, check_fn in providers:
            if check_fn(slug):
                return provider_name, slug
    return None, None


def _parse_args() -> tuple[list[tuple[str, "callable"]], int | None]:
    provider_filter = None
    limit = None
    for arg in sys.argv[1:]:
        if arg.startswith("--provider="):
            provider_filter = arg.split("=", 1)[1].lower()
        elif arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])

    providers = ALL_PROVIDERS
    if provider_filter:
        providers = [(name, fn) for name, fn in ALL_PROVIDERS if name == provider_filter]
        if not providers:
            valid = ", ".join(name for name, _ in ALL_PROVIDERS)
            print(f"Unknown provider '{provider_filter}'. Valid options: {valid}")
            sys.exit(1)
    return providers, limit


def _run_sweep(providers: list[tuple[str, "callable"]], limit: int | None) -> None:
    import csv

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from db.models import Company, utcnow
    from db.session import get_session, init_db

    init_db()

    csv_path = Path(__file__).parent.parent / "data" / "companies_remote_candidates.csv"
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]

    session = get_session()
    already_checked = {
        name for (name,) in session.query(Company.name).filter(Company.ats_checked_at.isnot(None)).all()
    }
    todo = [r["company_name"] for r in rows if r["company_name"] not in already_checked]
    print(f"{len(rows)} candidates, {len(already_checked)} already checked, {len(todo)} to probe "
          f"(providers: {', '.join(n for n, _ in providers)})")

    found_counts: dict[str, int] = {}
    done = 0

    def _worker(name: str) -> tuple[str, str | None, str | None]:
        provider, slug = detect(name, providers)
        return name, provider, slug

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(_worker, name): name for name in todo}
        for future in as_completed(futures):
            name, provider, slug = future.result()
            done += 1

            company = session.query(Company).filter(Company.name == name).first()
            if company is None:
                company = Company(name=name)
                session.add(company)
            if provider:
                company.ats_provider = provider
                company.ats_slug = slug
                found_counts[provider] = found_counts.get(provider, 0) + 1
            company.ats_checked_at = utcnow()
            session.commit()

            if done % 25 == 0 or done == len(todo):
                print(f"  {done}/{len(todo)} checked... ({sum(found_counts.values())} matches so far)")

    session.close()
    print("\nDone. Matches by provider:")
    for name, count in sorted(found_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {name}: {count}")
    print(f"  no match: {len(todo) - sum(found_counts.values())}")


if __name__ == "__main__":
    providers, limit = _parse_args()
    _run_sweep(providers, limit)
