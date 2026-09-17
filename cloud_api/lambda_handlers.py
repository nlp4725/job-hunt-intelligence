"""Lambda entry points (terraform/app/lambda.tf). Both run inside the VPC's
isolated subnets, with no internet access: they reach only Postgres and S3.

They sign in to Postgres with IAM database authentication: the function's own
AWS role signs a 15-minute token locally, so there is no password and no call
to Secrets Manager (which would need internet or a paid endpoint).

    rescore  SQS batches from jhi-rescore (score a captured job for every user, or
             rescore one user's board), and hourly {"type": "reconcile"}
    admin    invoked by hand:  aws lambda invoke --function-name jhi-admin-task \\
                                 --payload '{"command": "status"}' out.json
"""

import json
import os
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from cloud_api.observability import log_event, metric
from cloud_api.rescore import handle_message, run_reconcile
from cloud_api.settings import required


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
    """SQS batch: each message in its own transaction; failed ones are reported
    back so only they are retried (and moved to the dead-letter queue after 5
    tries). Or {"type": "reconcile"} from the hourly schedule."""
    engine = create_engine(iam_database_url("JHI_RESCORE_DB_USER"), connect_args={"options": "-c timezone=UTC"})
    try:
        if (event or {}).get("type") == "reconcile":
            with Session(engine) as db:
                report = run_reconcile(db)
            return {"stale_users": report.stale_users, "missing_scores": report.missing_scores}

        failures = []
        for record in (event or {}).get("Records", []):
            try:
                message = json.loads(record["body"])
                with Session(engine) as db:
                    handle_message(db, message)
                    db.commit()
            except Exception as exc:
                failures.append({"itemIdentifier": record.get("messageId")})
                metric("RescoreMessageFailed")
                log_event("rescore_message_failed", level="error", message_id=record.get("messageId"),
                          receive_count=record.get("attributes", {}).get("ApproximateReceiveCount"),
                          error_type=type(exc).__name__, error=str(exc)[:300])
        return {"batchItemFailures": failures}
    finally:
        engine.dispose()


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
