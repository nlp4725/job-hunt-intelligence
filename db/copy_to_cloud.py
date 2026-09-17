"""One-way copy of the local SQLite database into an empty cloud Postgres database.

    JHI_DATABASE_URL=postgresql+psycopg://user@host:5432/db \\
        ./venv/bin/python -m db.copy_to_cloud --source /path/to/data/job_hunt.db

Seeds the cloud once; it does not sync. The local file is snapshotted with
SQLite's backup API from a read-only connection, so it is never written and the
copy is consistent even while the local server keeps capturing. The target must
already be migrated (`alembic upgrade head`) and empty.

SQLite never enforced foreign keys here, so a child row can point at a parent
that no longer exists. Postgres would reject it: such rows are skipped and
reported, never silently dropped.
"""

import argparse
import os
import sqlite3
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import bindparam, create_engine, func, select, text

from db.models import Base

BATCH = 2000
DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "data" / "job_hunt.db"


@dataclass
class CopyResult:
    copied: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)   # orphan rows, and self-links to a skipped row


def _snapshot(sqlite_path: Path, into: Path) -> None:
    source = sqlite3.connect(f"file:{sqlite_path.resolve()}?mode=ro", uri=True)
    target = sqlite3.connect(into)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()


# Nasi's own resume text, career goals and chat history: never copied into the
# shared cloud tables (the cloud keeps resumes per user, encrypted).
PRIVATE_TABLES = frozenset({"resume", "career_goals", "chat_messages"})


def copy_sqlite_to_postgres(sqlite_path: Path, target_url: str, exclude: frozenset[str] = frozenset()) -> CopyResult:
    """`exclude`: table names to leave out (none of them may be a foreign-key parent of a copied table)."""
    sqlite_path = Path(sqlite_path)
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    result = CopyResult()
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "snapshot.db"
        _snapshot(sqlite_path, snapshot)
        source = create_engine(f"sqlite:///{snapshot}")
        target = create_engine(target_url, connect_args={"options": "-c timezone=UTC"})
        try:
            tables = [t for t in Base.metadata.sorted_tables if t.name not in exclude]
            with source.connect() as src, target.begin() as dst:
                for table in tables:
                    if dst.execute(select(func.count()).select_from(table)).scalar():
                        raise RuntimeError(f"Target table {table.name} is not empty: this copy only seeds an empty database.")
                copied_ids: dict[str, set] = {}
                for table in tables:
                    _copy_table(table, src, dst, copied_ids, result)
        finally:
            source.dispose()
            target.dispose()
    return result


def _copy_table(table, src, dst, copied_ids: dict[str, set], result: CopyResult) -> None:
    parent_fks = [(fk.parent.name, fk.column.table.name) for fk in table.foreign_keys if fk.column.table is not table]
    self_fks = [fk.parent.name for fk in table.foreign_keys if fk.column.table is table]
    ids, deferred, batch, copied, skipped = set(), defaultdict(list), [], 0, 0

    for row in src.execute(select(table)).mappings():
        row = dict(row)
        if any(row[col] is not None and row[col] not in copied_ids[parent] for col, parent in parent_fks):
            skipped += 1
            continue
        # A self-reference (jobs.duplicate_of_job_id) can point at a row not
        # inserted yet; set it after the whole table is in.
        for col in self_fks:
            if row[col] is not None:
                deferred[col].append({"_id": row["id"], "_value": row[col]})
                row[col] = None
        ids.add(row.get("id"))
        batch.append(row)
        if len(batch) >= BATCH:
            dst.execute(table.insert(), batch)
            copied += len(batch)
            batch = []
    if batch:
        dst.execute(table.insert(), batch)
        copied += len(batch)

    for col, links in deferred.items():
        valid = [link for link in links if link["_value"] in ids]
        skipped += len(links) - len(valid)
        if valid:
            dst.execute(table.update().where(table.c.id == bindparam("_id")).values({col: bindparam("_value")}), valid)

    if "id" in table.c:
        # Copied rows keep their ids; continue each id sequence past them.
        dst.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"
        ))
    copied_ids[table.name] = ids
    result.copied[table.name] = copied
    if skipped:
        result.skipped[table.name] = skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="local SQLite file (read-only)")
    args = parser.parse_args()
    url = os.environ.get("JHI_DATABASE_URL")
    if not url:
        parser.error("set JHI_DATABASE_URL to the target Postgres database")

    result = copy_sqlite_to_postgres(args.source, url)
    for name, count in result.copied.items():
        print(f"  {name:24s} {count}")
    if result.skipped:
        print("skipped (orphan rows or links to them):", result.skipped)


if __name__ == "__main__":
    main()
