"""Move classified seniority levels between databases (productization plan §3.3).

Levels live in the cloud-only job_seniority table, so the one-time SQLite
import (db/copy_to_cloud.py) does not carry them. Export from the machine that
classified the jobs, then import inside the cloud with the admin Lambda:

    JHI_DATABASE_URL=postgresql+psycopg://…/jhi_cloud \\
        ./venv/bin/python -m db.level_transfer --out /tmp/levels.jsonl.gz

Rows are keyed by the LinkedIn job id, never by internal ids, so the two
databases need nothing in common but the postings themselves.
"""

import argparse
import gzip
import json
import os
from pathlib import Path

from sqlalchemy import create_engine, text

FIELDS = ("level", "is_contract", "years_required", "inferred", "confidence", "evidence", "note", "prompt_version")
EXPORT_SQL = text(f"""
    SELECT j.job_id AS linkedin_id, {', '.join('s.' + f for f in FIELDS)}, s.classified_at
    FROM job_seniority s JOIN jobs j ON j.id = s.job_id
    ORDER BY j.job_id
""")
IMPORT_SQL = text(f"""
    INSERT INTO job_seniority (job_id, {', '.join(FIELDS)}, classified_at)
    SELECT j.id, {', '.join(':' + f for f in FIELDS)}, COALESCE(CAST(:classified_at AS timestamp), now() AT TIME ZONE 'utc')
    FROM jobs j WHERE j.job_id = :linkedin_id
    ON CONFLICT (job_id) DO UPDATE SET
        {', '.join(f'{f} = EXCLUDED.{f}' for f in FIELDS)}, classified_at = EXCLUDED.classified_at
""")


def export_levels(database_url: str, out_path: Path) -> int:
    """Write one JSON object per line, gzipped. Returns how many rows."""
    engine = create_engine(database_url)
    count = 0
    try:
        with engine.connect() as conn, gzip.open(out_path, "wt", encoding="utf-8") as out:
            for row in conn.execute(EXPORT_SQL).mappings():
                row = dict(row)
                row["classified_at"] = row["classified_at"].isoformat() if row["classified_at"] else None
                out.write(json.dumps(row) + "\n")
                count += 1
    finally:
        engine.dispose()
    return count


def import_levels(database_url: str, path: Path, batch: int = 1000) -> dict:
    """Upsert levels for jobs this database already has; rows for unknown
    postings are counted and skipped. Returns {"read", "written", "unknown"}."""
    engine = create_engine(database_url)
    read = written = 0
    try:
        with engine.begin() as conn, gzip.open(path, "rt", encoding="utf-8") as lines:
            rows = []
            for line in lines:
                row = json.loads(line)
                rows.append({"linkedin_id": str(row["linkedin_id"]), "classified_at": row.get("classified_at"),
                             **{f: row.get(f) for f in FIELDS}})
                read += 1
                if len(rows) >= batch:
                    written += conn.execute(IMPORT_SQL, rows).rowcount
                    rows = []
            if rows:
                written += conn.execute(IMPORT_SQL, rows).rowcount
    finally:
        engine.dispose()
    return {"read": read, "written": written, "unknown": read - written}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="gzipped JSON lines to write")
    args = parser.parse_args()
    url = os.environ.get("JHI_DATABASE_URL")
    if not url:
        parser.error("set JHI_DATABASE_URL to the database holding job_seniority")
    print(f"{export_levels(url, args.out)} levels → {args.out}", flush=True)


if __name__ == "__main__":
    main()
