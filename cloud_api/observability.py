"""Structured logs and metrics for the API, workers and Lambda functions
(productization plan §1.5.15).

Everything goes to stdout as one JSON object per line. On AWS, ECS and Lambda
ship stdout to CloudWatch Logs; metric lines use CloudWatch Embedded Metric
Format (EMF), so CloudWatch turns them into metrics with no agent and no API
call (which matters for the Lambdas, which have no internet access).

Never log emails, resume text, job posting text or tokens: log ids.

    log_event("capture", outcome="scored", job_id=12)
    metric("CaptureOutcome", dimensions={"Outcome": "scored"})
"""

import json
import sys
import time

NAMESPACE = "JHI"
FORBIDDEN_FIELDS = frozenset({"email", "resume", "text", "raw_text", "token", "password", "authorization"})


def _write(record: dict) -> None:
    sys.stdout.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def log_event(event: str, level: str = "info", **fields) -> None:
    """One structured log line. Fields that could carry personal data are dropped."""
    safe = {k: v for k, v in fields.items() if k.lower() not in FORBIDDEN_FIELDS}
    _write({"ts": round(time.time(), 3), "level": level, "event": event, **safe})


def metric(name: str, value: float = 1, unit: str = "Count", dimensions: dict[str, str] | None = None) -> None:
    """One CloudWatch metric data point via EMF. Keep dimensions low-cardinality
    (an outcome or a message type, never a user or job id)."""
    dimensions = {k: str(v) for k, v in (dimensions or {}).items()}
    _write({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [list(dimensions)] if dimensions else [[]],
                "Metrics": [{"Name": name, "Unit": unit}],
            }],
        },
        name: value,
        **dimensions,
    })


class Timer:
    """with Timer() as t: ...; t.ms"""

    def __enter__(self):
        self._start = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self._start) * 1000, 1)
        return False
