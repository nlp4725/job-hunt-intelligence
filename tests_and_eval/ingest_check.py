"""Data-quality gate for the job pipeline. Run after every collection run and
after any bulk write.

    ./venv/bin/python -m tests_and_eval.ingest_check              # full audit
    ./venv/bin/python -m tests_and_eval.ingest_check --hours 6    # this run only
    ./venv/bin/python -m tests_and_eval.ingest_check --json       # machine-readable

Exit codes: 0 clean, 1 warnings only, 2 at least one BLOCK violation.

Two severities, and the distinction is the whole point:

  BLOCK  An invariant that cannot be violated by any correct execution. A
         non-zero count is a BUG, not bad luck — a dangling foreign key, a
         value outside its enum, a timestamp that precedes the row's own
         creation. These are absolute; there is no acceptable rate.

  WARN   A field that is best-effort by nature, tracked as a RATE against a
         baseline. LinkedIn genuinely does not state a workplace type on every
         posting, so "never null" is not achievable and demanding it would only
         invite fabricated values. What matters is the rate moving.

The rates below were measured on 2026-09-08 across 12,470 detail-fetched rows.
Update them deliberately, with the reason, when a real improvement lands —
never to silence a check.
"""

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from db.session import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

BLOCK, WARN = "BLOCK", "WARN"

# (name, severity, sql returning one count, baseline rate or None, note)
# A WARN check's SQL returns the numerator; `denom` supplies the denominator.
CHECKS = [
    # -- referential integrity: no correct write can produce these -----------
    ("duplicate_of points at a missing job", BLOCK,
     "select count(*) from jobs j where j.duplicate_of_job_id is not null "
     "and not exists(select 1 from jobs t where t.id=j.duplicate_of_job_id)", None,
     "a repost link surviving the deletion of its target"),
    ("screening_result orphaned from its job", BLOCK,
     "select count(*) from screening_results r "
     "where not exists(select 1 from jobs j where j.id=r.job_id)", None,
     "score with no job — invisible on the dashboard, still billed for"),
    ("company_id points at a missing company", BLOCK,
     "select count(*) from jobs j where j.company_id is not null "
     "and not exists(select 1 from companies c where c.id=j.company_id)", None, ""),

    # -- uniqueness ---------------------------------------------------------
    ("same LinkedIn job_id stored twice", BLOCK,
     "select count(*) from (select job_id from jobs group by job_id having count(*)>1)", None,
     "upsert key broken — dedup and repost counting both become meaningless"),
    ("job with more than one screening_result", BLOCK,
     "select count(*) from (select job_id from screening_results group by job_id having count(*)>1)", None,
     "double-billed LLM call, and which score wins is undefined"),

    # -- value domains ------------------------------------------------------
    ("workplace_type outside Remote/Hybrid/On-site", BLOCK,
     "select count(*) from jobs where workplace_type is not null "
     "and workplace_type not in ('Remote','Hybrid','On-site')", None,
     "dashboard filters match on exact strings"),
    ("total_score != skill + seniority + expertise", BLOCK,
     "select count(*) from screening_results where total_score is not null "
     "and skill_score is not null and seniority_score is not null and expertise_score is not null "
     "and total_score != skill_score+seniority_score+expertise_score", None,
     "ranking is derived from this sum"),
    ("component score outside 0-5", BLOCK,
     "select count(*) from screening_results where skill_score not between 0 and 5 "
     "or seniority_score not between 0 and 5 or expertise_score not between 0 and 5", None,
     "an LLM returning an out-of-range score silently skews every ranking"),

    # -- temporal sanity ----------------------------------------------------
    ("last_seen_at earlier than first_seen_at", BLOCK,
     "select count(*) from jobs where last_seen_at < first_seen_at", None, ""),
    ("applied_at earlier than the row existed", BLOCK,
     "select count(*) from jobs where applied_at is not null and applied_at < first_seen_at", None,
     "you cannot apply to a job before it was collected"),
    ("posted_date_seen_at in the future", BLOCK,
     "select count(*) from jobs where posted_date_seen_at > datetime('now')", None,
     "a future anchor makes every relative date resolve wrong"),

    # -- application bookkeeping (invariants 4, 5) --------------------------
    ("applied without applied_at, since the column existed", BLOCK,
     "select count(*) from jobs where applied=1 and applied_at is null "
     "and first_seen_at > '2026-07-29'", None,
     "204 rows predating 2026-07-29 are excluded: that data was never recorded"),
    ("applied with no company_name", WARN,
     "select count(*) from jobs where applied=1 and (company_name is null or trim(company_name)='')",
     0.005, "these cannot be counted in the per-company applied tally at all"),

    # -- collection completeness (invariant 1) ------------------------------
    ("detail-fetched job with no title", BLOCK,
     "select count(*) from jobs where detail_fetched=1 and (title is null or title='')", None,
     "detail_fetched=1 asserts title AND raw_text were captured"),
    ("detail-fetched job with no raw_text", BLOCK,
     "select count(*) from jobs where detail_fetched=1 and (raw_text is null or raw_text='')", None, ""),
    ("no posted_date text", WARN,
     "select count(*) from jobs where detail_fetched=1 and (posted_date is null or posted_date='')",
     0.002, "dashboard drops any row whose date will not parse"),
    ("no company_name", WARN,
     "select count(*) from jobs where detail_fetched=1 and (company_name is null or trim(company_name)='')",
     0.02, ""),
    ("no location", WARN,
     "select count(*) from jobs where detail_fetched=1 and (location is null or location='')",
     0.05, ""),
    ("no company industry", WARN,
     "select count(*) from jobs j left join companies c on j.company_id=c.id "
     "where j.detail_fetched=1 and (c.industry is null or trim(c.industry)='')",
     0.084, "the agency blocklist is DERIVED from companies.industry — a company with no "
            "industry can never be recognised as a staffing agency, so its postings burn "
            "LLM screening calls. The 2026-09-08 run hit 32% with nothing reporting it, "
            "because industry was in neither this gate nor the extraction telemetry; the "
            "cause was LinkedIn rendering it as a bare text node beside sibling spans, "
            "invisible to a childless-element scan (fixed 2026-09-09). Expect this "
            "baseline to fall well below 8.4% on runs after that fix"),
    ("no company size", WARN,
     "select count(*) from jobs j left join companies c on j.company_id=c.id "
     "where j.detail_fetched=1 and (c.size is null or c.size='')",
     0.04, "About-card did not render, or company row was never created"),
    ("workplace_type unresolved", WARN,
     "select count(*) from jobs where detail_fetched=1 and workplace_type is null",
     0.55, "NULL conflates 'LinkedIn did not say' with 'we failed to read it' "
           "— see the note on the Unknown sentinel"),
    ("posted_date with no anchor recorded", WARN,
     "select count(*) from jobs where detail_fetched=1 and posted_date is not null "
     "and posted_date_seen_at is null",
     0.03, "349 known: anchor destroyed by the 2026-09-08 backfill, unrecoverable"),
]

DENOM = "select count(*) from jobs where detail_fetched=1"  # scoped by _scoped() when --hours is given


def _scoped(sql, hours):
    """Narrow a check to rows created in the last `hours`, where that is
    meaningful and safe.

    Only checks whose FROM clause is the jobs table can be time-filtered, and
    the column has to carry that query's own alias. Blindly appending
    `first_seen_at > ...` broke the run outright — several checks select from
    screening_results, which has no such column ("no such column:
    first_seen_at", 2026-09-08). Anything not recognised here runs unscoped:
    a full-table result is always correct, just broader than asked for, whereas
    a guessed alias is a crash.
    """
    if not hours:
        return sql
    normalized = " ".join(sql.lower().split())
    # Anchor on the TOP-LEVEL from clause. A plain substring test matched
    # "from jobs j" inside a subquery — `select count(*) from screening_results
    # r where not exists(select 1 from jobs j ...)` scoped itself on
    # j.first_seen_at and crashed, which is how this was found.
    match = re.match(r"select count\(\*\) from jobs(?: (j))?\b", normalized)
    if not match:
        return sql
    if "where" not in normalized or "group by" in normalized:
        return sql
    column = "j.first_seen_at" if match.group(1) else "first_seen_at"
    return f"{sql} and {column} > datetime('now', '-{int(hours)} hours')"


def run(hours=None, as_json=False):
    session = SessionLocal()

    # The denominator must be scoped the same way the numerators are, or a rate
    # is nonsense: 2 unresolved fields out of the last 6 hours' captures, divided
    # by all 12,470 rows ever collected, reads as 0.0% and no WARN can ever fire.
    # That would make the wrapper's --hours gate silently useless.
    denom = session.execute(text(_scoped(DENOM, hours))).scalar() or 1
    results = []
    for name, severity, sql, baseline, note in CHECKS:
        count = session.execute(text(_scoped(sql, hours))).scalar() or 0
        rate = count / denom if baseline is not None else None
        if severity == BLOCK:
            ok = count == 0
        else:
            ok = rate <= baseline * 1.25  # 25% headroom before a rate is "moving"
        results.append({
            "check": name, "severity": severity, "count": count,
            "rate": rate, "baseline": baseline, "ok": ok, "note": note,
        })

    if as_json:
        print(json.dumps({"denominator": denom, "results": results}, indent=2))
    else:
        _print_table(results, denom)

    if any(r["severity"] == BLOCK and not r["ok"] for r in results):
        return 2
    return 1 if any(not r["ok"] for r in results) else 0


def _print_table(results, denom):
    print(f"data-quality gate — {denom} detail-fetched jobs\n")
    for severity in (BLOCK, WARN):
        rows = [r for r in results if r["severity"] == severity]
        print(f"  {severity}")
        for r in rows:
            mark = "ok  " if r["ok"] else "FAIL"
            if r["rate"] is None:
                detail = f"{r['count']}"
            else:
                detail = f"{r['count']} ({r['rate']:.1%}, baseline {r['baseline']:.1%})"
            print(f"    {mark}  {r['check']:<48} {detail}")
            if not r["ok"] and r["note"]:
                print(f"          -> {r['note']}")
        print()
    bad = [r for r in results if not r["ok"]]
    print(f"{len(bad)} failing / {len(results)} checks" if bad else "all checks passed")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=None, help="restrict row-level checks to the last N hours")
    p.add_argument("--json", action="store_true")
    sys.exit(run(hours=p.parse_args().hours, as_json=p.parse_args().json))
