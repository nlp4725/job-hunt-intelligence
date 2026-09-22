"""Delta copy: the local rows the cloud does not have yet.

    aws s3 cp /tmp/job_hunt_export.db s3://$(terraform output -raw import_bucket)/imports/job_hunt.db
    aws lambda invoke --function-name jhi-admin-task \\
        --payload '{"command": "backfill_sqlite", "s3_key": "imports/job_hunt.db"}' \\
        --cli-binary-format raw-in-base64-out out.json && cat out.json

copy_to_cloud.py seeds an *empty* database and refuses a non-empty one, which is
right for a first import and useless afterwards. This is the follow-up: it takes
the same SQLite export and inserts only what is missing, leaving every existing
row untouched. Re-running it is a no-op.

**How rows are matched.** By primary key. The seed copied rows with their local
ids and then continued each Postgres sequence past them, so for seeded data the
two databases agree on what id 12345 means. That is what makes a delta by id
sound — and it stops being true the moment the cloud starts creating rows of its
own (a capture posted straight to the API). `assert_ids_line_up` checks the
assumption on every run and refuses rather than writing a row under an id that
already means something else up there.

So: use this to catch the cloud up on collection that happened before the
extension started posting to it. Once captures go to the cloud directly, rows
are born there and the local copy becomes the derived one — at which point the
flow reverses and this script has done its job.
"""

import argparse
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import bindparam, create_engine, select, text

from db.copy_to_cloud import BATCH, DEFAULT_SOURCE, PRIVATE_TABLES, _snapshot
from db.models import Base

# How many ids to compare before trusting that local and cloud mean the same
# thing by an id. Both ends of the range: the oldest rows came from the seed,
# the newest are where a cloud-side insert would first collide.
PROBE = 250


@dataclass
class BackfillResult:
    inserted: dict[str, int] = field(default_factory=dict)
    already_there: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)   # orphans, and links to them


class IdsDiverged(RuntimeError):
    """The cloud has rows this script did not put there, so ids no longer match."""


def backfill_sqlite_to_postgres(sqlite_path: Path, target_url: str,
                                exclude: frozenset[str] = frozenset()) -> BackfillResult:
    sqlite_path = Path(sqlite_path)
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    result = BackfillResult()
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "snapshot.db"
        _snapshot(sqlite_path, snapshot)                     # read-only; the local app keeps capturing
        source = create_engine(f"sqlite:///{snapshot}")
        target = create_engine(target_url, connect_args={"options": "-c timezone=UTC"})
        try:
            tables = [t for t in Base.metadata.sorted_tables if t.name not in exclude]
            with source.connect() as src, target.begin() as dst:
                assert_ids_line_up(src, dst)
                known: dict[str, set] = {}
                for table in tables:
                    _backfill_table(table, src, dst, known, result)
        finally:
            source.dispose()
            target.dispose()
    return result


def assert_ids_line_up(src, dst) -> None:
    """Refuse the copy unless the two databases still agree on what a job id means.

    Compares the LinkedIn job_id behind the same primary key at both ends of the
    id range. A mismatch means the cloud minted ids of its own, and inserting by
    id from here would attach this job's rows to a different job's id.
    """
    jobs = Base.metadata.tables["jobs"]
    ends = (select(jobs.c.id, jobs.c.job_id).order_by(jobs.c.id).limit(PROBE),
            select(jobs.c.id, jobs.c.job_id).order_by(jobs.c.id.desc()).limit(PROBE))
    local = {row.id: row.job_id for query in ends for row in src.execute(query)}
    if not local:
        return
    rows = dst.execute(select(jobs.c.id, jobs.c.job_id).where(jobs.c.id.in_(list(local)))).all()
    mismatched = [(row.id, local[row.id], row.job_id) for row in rows if local[row.id] != row.job_id]
    if mismatched:
        job_id, mine, theirs = mismatched[0]
        raise IdsDiverged(
            f"{len(mismatched)} id(s) mean different jobs in the two databases "
            f"(id {job_id}: local LinkedIn job {mine}, cloud {theirs}). "
            "The cloud has rows this script did not seed, so a delta by id is no longer safe.")


def _backfill_table(table, src, dst, known: dict[str, set], result: BackfillResult) -> None:
    """Insert this table's missing rows, skipping any whose parent row is missing."""
    present = {row[0] for row in dst.execute(select(table.c.id))} if "id" in table.c else set()
    parent_fks = [(fk.parent.name, fk.column.table.name) for fk in table.foreign_keys if fk.column.table is not table]
    self_fks = [fk.parent.name for fk in table.foreign_keys if fk.column.table is table]
    usable = dict(known)                                     # parents already up there, plus ones inserted now
    inserted_ids, deferred, batch = set(), defaultdict(list), []
    inserted = already = skipped = 0

    for row in src.execute(select(table)).mappings():
        row = dict(row)
        if row.get("id") in present:
            already += 1
            continue
        if any(row[col] is not None and row[col] not in usable[parent] for col, parent in parent_fks):
            skipped += 1                                     # parent never made it (orphan in the source)
            continue
        # A self-reference (jobs.duplicate_of_job_id) may point at a row this
        # pass has not inserted yet; fill it in once the whole table is in.
        for col in self_fks:
            if row[col] is not None:
                deferred[col].append({"_id": row["id"], "_value": row[col]})
                row[col] = None
        inserted_ids.add(row.get("id"))
        batch.append(row)
        if len(batch) >= BATCH:
            dst.execute(table.insert(), batch)
            inserted += len(batch)
            batch = []
    if batch:
        dst.execute(table.insert(), batch)
        inserted += len(batch)

    for col, links in deferred.items():
        # The target of a self-link is fine whether it was already up there or
        # arrived in this pass; anything else would dangle.
        valid = [link for link in links if link["_value"] in inserted_ids or link["_value"] in present]
        skipped += len(links) - len(valid)
        if valid:
            dst.execute(table.update().where(table.c.id == bindparam("_id")).values({col: bindparam("_value")}), valid)

    if "id" in table.c:
        # Inserted rows keep their ids, so the sequence has to clear them or the
        # cloud's own next insert collides.
        dst.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"
        ))
    known[table.name] = present | inserted_ids
    result.inserted[table.name] = inserted
    result.already_there[table.name] = already
    if skipped:
        result.skipped[table.name] = skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="local SQLite file (read-only)")
    parser.add_argument("--keep-private", action="store_true",
                        help="include the owner's private tables (the Lambda never does)")
    args = parser.parse_args()
    url = os.environ.get("JHI_DATABASE_URL")
    if not url:
        parser.error("set JHI_DATABASE_URL to the target Postgres database")

    result = backfill_sqlite_to_postgres(args.source, url,
                                         exclude=frozenset() if args.keep_private else PRIVATE_TABLES)
    for name, count in result.inserted.items():
        if count or result.already_there.get(name):
            print(f"  {name:24s} +{count:<7d} (already there: {result.already_there.get(name, 0)})")
    if result.skipped:
        print("skipped (orphan rows or links to them):", result.skipped)


if __name__ == "__main__":
    main()
