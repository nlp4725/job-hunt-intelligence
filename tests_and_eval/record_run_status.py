"""Write scraper/logs/last_run_status.json — the dashboard's run record.

The scheduled wrapper writes this automatically. An interactive screen (the
usual case, since the skill needs a paired browser and therefore cannot be
scheduled) has no wrapper around it, so it calls this at the end of its report
instead. Without it the strip keeps showing whatever the last SCHEDULED run
said, which may be days old and wrong.

    ./venv/bin/python -m tests_and_eval.record_run_status --state ok \
        --detail "312 jobs across 20 pages"

States match the wrapper's: ok, chrome-closed, run-failed, no-data,
block-violation, extraction-drift, incomplete.
"""

import argparse
import json
import pathlib
from datetime import datetime, timezone

STATUS = pathlib.Path(__file__).resolve().parent.parent / "scraper" / "logs" / "last_run_status.json"

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--detail", default="")
    p.add_argument("--keyword", default="")
    args = p.parse_args()
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({
        "state": args.state,
        "detail": args.detail,
        "keyword": args.keyword,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "log": "interactive session (no wrapper log)",
    }) + "\n")
    print(f"recorded run status: {args.state}")
