"""Lambda entry points (terraform/app/lambda.tf). Both run inside the VPC's
isolated subnets, with no internet access: they reach only Postgres and S3.

They sign in to Postgres with IAM database authentication: the function's own
AWS role signs a 15-minute token locally, so there is no password and no call
to Secrets Manager (which would need internet or a paid endpoint).

    rescore  every 5 minutes: score newly captured jobs for every user
    admin    invoked by hand:  aws lambda invoke --function-name jhi-admin-task \\
                                 --payload '{"command": "status"}' out.json
"""

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from cloud_api.rescore_worker import RescoreReport, drain_rescore_queue
from cloud_api.settings import required

SAFETY_MARGIN_MS = 60_000     # stop starting new batches a minute before the timeout


def iam_database_url(user_var: str) -> str:
    """JHI_DATABASE_URL when set (tests, local runs); otherwise an IAM auth token for the login in `user_var`."""
    if os.environ.get("JHI_DATABASE_URL"):
        return os.environ["JHI_DATABASE_URL"]
    import boto3

    host, port, region, user = required("DB_HOST"), int(os.environ.get("DB_PORT", "5432")), required("AWS_REGION"), required(user_var)
    token = boto3.client("rds", region_name=region).generate_db_auth_token(
        DBHostname=host, Port=port, DBUsername=user, Region=region)
    return URL.create("postgresql+psycopg", username=user, password=token, host=host, port=port,
                      database=required("DB_NAME"), query={"sslmode": "require"}).render_as_string(hide_password=False)


def rescore(event, context) -> dict:
    """Drain rescore_queue in batches until it is empty or time is nearly up."""
    url = iam_database_url("JHI_RESCORE_DB_USER")
    total = RescoreReport()
    while True:
        report = drain_rescore_queue(url)
        total.jobs += report.jobs
        total.scores += report.scores
        if report.jobs == 0 or (context is not None and context.get_remaining_time_in_millis() < SAFETY_MARGIN_MS):
            break
    print(json.dumps({"rescore": asdict(total)}), flush=True)
    return asdict(total)


# --- admin tasks -------------------------------------------------------------------

def _status(url: str) -> dict:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return {
                "jobs": conn.execute(text("SELECT count(*) FROM jobs")).scalar(),
                "jobs_last_24h": conn.execute(text(
                    "SELECT count(*) FROM jobs WHERE first_seen_at > (now() AT TIME ZONE 'utc') - interval '1 day'")).scalar(),
                "jobs_with_level": conn.execute(text("SELECT count(*) FROM job_seniority")).scalar(),
                "rescore_queue": conn.execute(text("SELECT count(*) FROM rescore_queue")).scalar(),
            }
    finally:
        engine.dispose()


def _import_sqlite(url: str, key) -> dict:
    """Seed an empty cloud database from a SQLite export uploaded to the import bucket,
    leaving out Nasi's private tables, then delete the upload."""
    import boto3

    from db.copy_to_cloud import PRIVATE_TABLES, copy_sqlite_to_postgres

    if not isinstance(key, str) or not key.startswith("imports/") or ".." in key:
        raise ValueError("s3_key must be under imports/")
    bucket = required("IMPORT_BUCKET")
    s3 = boto3.client("s3", region_name=required("AWS_REGION"))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "job_hunt.db"
        s3.download_file(bucket, key, str(path))
        result = copy_sqlite_to_postgres(path, url, exclude=PRIVATE_TABLES)
    s3.delete_object(Bucket=bucket, Key=key)
    return {"copied": result.copied, "skipped": result.skipped, "left_out": sorted(PRIVATE_TABLES)}


COMMANDS = {"status": lambda url, event: _status(url),
            "import_sqlite": lambda url, event: _import_sqlite(url, event.get("s3_key"))}


def admin(event, context) -> dict:
    """A fixed list of commands, never arbitrary SQL."""
    command = (event or {}).get("command")
    if command not in COMMANDS:
        raise ValueError(f"command must be one of: {', '.join(sorted(COMMANDS))}")
    result = COMMANDS[command](iam_database_url("JHI_ADMIN_TASK_DB_USER"), event)
    print(json.dumps({"admin": command, "result": result}), flush=True)
    return result
