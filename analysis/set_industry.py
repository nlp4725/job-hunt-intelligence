"""Manually record a company's industry.

    ./venv/bin/python -m analysis.set_industry "Tanium" "Computer and Network Security"
    ./venv/bin/python -m analysis.set_industry --list          # what values already exist
    ./venv/bin/python -m analysis.set_industry --missing       # companies still lacking one

Why a manual path exists at all: 677 companies have no industry, and it is not
recoverable offline — raw_text holds the job description and page chrome, never
the About-the-company card, so the value was never captured. Re-visiting each
company on LinkedIn is slow and risks a block. When you happen to be looking at
a posting anyway, recording it takes three seconds.

The field is load-bearing: judge/agency_blocklist.py derives its staffing-agency
list from Company.industry, so a company without one can never be recognised as
a recruiting intermediary and its postings consume LLM screening calls.

An unrecognised value warns but is still written — LinkedIn adds industries, and
refusing a real new one would be worse than accepting a typo. The warning is
there to catch the failure mode actually observed: page section headers and
taglines landing in this field ("Commitments", "Interested in working with us in
the future?" — 207 companies, cleaned 2026-09-09).
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from db.models import Company, Job  # noqa: E402
from db.session import SessionLocal  # noqa: E402
from sqlalchemy import func  # noqa: E402


def known_industries(session):
    return {
        row[0]
        for row in session.query(Company.industry)
        .filter(Company.industry.isnot(None), Company.industry != "")
        .distinct()
        .all()
    }


def set_industry(name, industry):
    session = SessionLocal()
    known = known_industries(session)

    matches = session.query(Company).filter(Company.name == name).all()
    if not matches:
        # Fall back to a case-insensitive contains, so a partial name works.
        matches = session.query(Company).filter(Company.name.ilike(f"%{name}%")).all()
    if not matches:
        print(f"no company matching {name!r}")
        return 1
    if len(matches) > 1:
        print(f"{len(matches)} companies match {name!r} — be more specific:")
        for c in matches[:10]:
            print(f"   {c.name}   (industry={c.industry!r})")
        return 1

    company = matches[0]
    if industry not in known:
        print(f"note: {industry!r} is not among the {len(known)} industries already in the DB.")
        print("      Writing it anyway — check it is LinkedIn's own label, not a page heading.")

    before = company.industry
    company.industry = industry
    session.commit()

    jobs = session.query(func.count(Job.id)).filter(Job.company_id == company.id).scalar()
    print(f"{company.name}: {before!r} -> {industry!r}   ({jobs} job(s) affected)")
    if industry == "Staffing and Recruiting":
        print("  this company is now caught by the agency blocklist")
    return 0


def list_known():
    session = SessionLocal()
    rows = (
        session.query(Company.industry, func.count(Company.id))
        .filter(Company.industry.isnot(None), Company.industry != "")
        .group_by(Company.industry)
        .order_by(func.count(Company.id).desc())
        .all()
    )
    print(f"{len(rows)} industry values in use:\n")
    for value, n in rows:
        print(f"   {n:>4}  {value}")
    return 0


def list_missing(limit=40):
    session = SessionLocal()
    rows = (
        session.query(Company.name, func.count(Job.id).label("n"))
        .join(Job, Job.company_id == Company.id)
        .filter((Company.industry.is_(None)) | (Company.industry == ""))
        .group_by(Company.id)
        .order_by(func.count(Job.id).desc())
        .limit(limit)
        .all()
    )
    total = (
        session.query(func.count(Company.id))
        .filter((Company.industry.is_(None)) | (Company.industry == ""))
        .scalar()
    )
    print(f"{total} companies have no industry; the {len(rows)} with the most postings:\n")
    for name, n in rows:
        print(f"   {n:>3} jobs  {name}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Record a company's LinkedIn industry by hand.")
    p.add_argument("company", nargs="?", help="company name (exact, or a unique substring)")
    p.add_argument("industry", nargs="?", help="LinkedIn's industry label")
    p.add_argument("--list", action="store_true", help="show industry values already in use")
    p.add_argument("--missing", action="store_true", help="companies with no industry, most postings first")
    a = p.parse_args()

    if a.list:
        sys.exit(list_known())
    if a.missing:
        sys.exit(list_missing())
    if not a.company or not a.industry:
        p.error("give a company and an industry, or use --list / --missing")
    sys.exit(set_industry(a.company, a.industry))
