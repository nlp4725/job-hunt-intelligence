"""Rescoring pipeline (productization plan §1.5.9).

    API request ──commit──► publish message ──► SQS jhi-rescore ──► λ jhi-rescore ──► user_job_scores
                                                     └─ 5 failed receives ─► jhi-rescore-dlq (alarm)
    hourly ──► λ jhi-rescore {"type": "reconcile"}: anything a lost message left unscored

Two message types:
    {"type": "job",  "job_id": 123}   a captured job: score it for every ready user
    {"type": "user", "user_id": 42}   a profile or resume change: rescore that user's whole board

Messages are published only after the request's transaction commits (never
for work that rolled back). Scoring is an upsert keyed by (user, job), so a
message delivered twice is harmless. If publishing fails, the request still
succeeds: the failure is logged and counted, and reconciliation catches up.
"""

import json

from flask import g, has_request_context
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from analysis.rescoring import MESSAGE_TYPES, job_message, process_message, reconcile, user_message  # noqa: F401  (re-exported)
from cloud_api.observability import log_event, metric


def queue_after_commit(message: dict) -> None:
    """Called inside a request; cloud_api/app.py publishes once the transaction commits."""
    if message.get("type") not in MESSAGE_TYPES:
        raise ValueError(f"unknown rescore message: {message}")
    if has_request_context():
        g.setdefault("rescore_messages", []).append(message)


class SqsPublisher:
    def __init__(self, queue_url: str, client=None, region: str | None = None):
        import boto3

        self.queue_url = queue_url
        self.client = client or boto3.client("sqs", region_name=region)

    def publish(self, messages: list[dict]) -> None:
        for start in range(0, len(messages), 10):
            chunk = messages[start:start + 10]
            entries = [{"Id": str(i), "MessageBody": json.dumps(m)} for i, m in enumerate(chunk)]
            response = self.client.send_message_batch(QueueUrl=self.queue_url, Entries=entries)
            if response.get("Failed"):
                retry = [e for e in entries if e["Id"] in {f["Id"] for f in response["Failed"]}]
                response = self.client.send_message_batch(QueueUrl=self.queue_url, Entries=retry)
                if response.get("Failed"):
                    raise RuntimeError(f"{len(response['Failed'])} rescore messages not accepted by SQS")


class InlinePublisher:
    """Local development and tests: process messages immediately with a login
    that may score every user (the owner, or one in the jhi_scorer role)."""

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, connect_args={"options": "-c timezone=UTC"})

    def publish(self, messages: list[dict]) -> None:
        with Session(self.engine) as db:
            for message in messages:
                handle_message(db, message)
                db.commit()


def handle_message(db, message: dict):
    """process_message plus logs and metrics; used by the Lambda and InlinePublisher."""
    result = process_message(db, message)
    if result.missing:
        log_event("rescore_target_missing", level="warning", message=message)
    metric("RescoreMessages", dimensions={"Type": result.kind})
    metric("ScoresWritten", result.scores)
    return result


def run_reconcile(db):
    report = reconcile(db)
    metric("ReconcileStaleUsers", report.stale_users)
    metric("ReconcileMissingScores", report.missing_scores)
    metric("UnscoredJobAgeMinutes", round(report.oldest_unscored_minutes, 1), unit="None")
    log_event("reconcile", stale_users=report.stale_users, missing_scores=report.missing_scores,
              oldest_unscored_minutes=round(report.oldest_unscored_minutes, 1))
    return report


