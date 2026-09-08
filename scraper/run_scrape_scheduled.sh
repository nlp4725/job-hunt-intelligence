#!/bin/bash
# Wrapper for launchd (see ~/Library/LaunchAgents/com.jobhunt.scrape.plist).
# launchd's StartCalendarInterval only supports a fixed trigger time, not a
# randomized window — so this fires at a fixed anchor (4:00 PM) and sleeps a
# random amount within the remaining 3-hour window (4-7 PM) before actually
# running the scraper, rather than always scraping at exactly 4:00 PM.

set -euo pipefail

PROJECT_DIR="/Users/nasi/job_hunt_intelligence"
LOG_DIR="$PROJECT_DIR/scraper/logs"
mkdir -p "$LOG_DIR"

WINDOW_SECONDS=$((3 * 60 * 60))   # 4-7 PM window
DELAY=$((RANDOM % WINDOW_SECONDS))

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — scheduled run triggered, sleeping ${DELAY}s before scraping ==="
} >> "$LOG_DIR/scheduled_wrapper.log"

sleep "$DELAY"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — sleep done, starting scrape ==="
} >> "$LOG_DIR/scheduled_wrapper.log"

cd "$PROJECT_DIR"
# --week (7-day lookback) instead of the default 24h window: the run fires
# daily but at a jittered time (see above), so a 24h window can miss
# listings posted between "yesterday's actual run time" and "today's
# scheduled anchor" whenever the jitter drifts late one day and early the
# next. A week-wide window overlaps every prior run, so dedup_page's
# existing repost/already-seen handling absorbs the redundancy for free.
"$PROJECT_DIR/venv/bin/python3" scraper/run_scrape.py --week >> "$LOG_DIR/scheduled_wrapper.log" 2>&1

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') — scrape run finished ==="
} >> "$LOG_DIR/scheduled_wrapper.log"
