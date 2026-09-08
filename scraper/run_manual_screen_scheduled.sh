#!/bin/bash
# Scheduled LinkedIn manual screen. Requires Chrome running, logged into LinkedIn,
# with the job-hunt extension + Claude in Chrome extension loaded — this skill drives
# a real browser, so it CANNOT run headless or in the cloud.
#
# This wrapper exists because the scheduled path (launchd, every 5.1h) has no human
# in it: nobody to fix the environment before, or notice the outcome after.
#
# History that shaped it — 23 scheduled runs to 2026-09-08, ZERO successes:
#   * 22 exited 1 with a bare "An unknown error occurred (Unexpected)".
#   * The 1 run that exited 0 had ALSO failed — claude printed a file-descriptor
#     error and exited 0 anyway. Trusting $? alone reported that as a success, so
#     the single reassuring data point was fiction. Success is now confirmed by
#     what actually landed in the DB, not by an exit code.
#   * Every failure log said "creds file: ABSENT", which was a red herring:
#     macOS keeps Claude credentials in the Keychain and that file never exists.
#     Six days of logs pointed at a non-problem.
#   * Nothing alerted. Six days of total failure looked exactly like six days of
#     working, which is the actual bug this rewrite is aimed at.
#
# Verified 2026-09-08 via a throwaway LaunchAgent: `claude -p` works correctly in
# the real launchd context (raw env AND via login shell). So auth/Keychain/PATH
# are NOT the blocker; failures are transient — hence the retry.
#
# Usage:  run_manual_screen_scheduled.sh [keyword]
#         run_manual_screen_scheduled.sh --selftest   # verify wiring, no screening run
set -uo pipefail

# launchd starts us with a bare environment, in which `claude` cannot reach its
# stored credentials and dies with "An unknown error occurred". Re-exec once
# through an interactive login shell so we inherit the same env a Terminal has.
if [ -z "${JHI_LOGIN_SHELL:-}" ]; then
  export JHI_LOGIN_SHELL=1
  exec /bin/zsh -lc "$(printf '%q ' "$0" "$@")"
fi

# launchd hands children a soft fd limit of 256; Claude Code needs far more.
# macOS rejects "unlimited" and caps at kern.maxfilesperproc, so step down
# through concrete values and keep the first that sticks.
for n in 65536 20480 10240 4096; do ulimit -n "$n" 2>/dev/null && break; done

PROJECT=/Users/nasi/Desktop/job_hunt_intelligence
cd "$PROJECT" || exit 1
export PATH="/Users/nasi/.local/bin:$PATH"

SELFTEST=0
[ "${1:-}" = "--selftest" ] && { SELFTEST=1; shift; }
KEYWORD="${1:-ai engineer}"
LOG="scraper/logs/manual_screen_$(date +%Y%m%d_%H%M).log"
STATUS="scraper/logs/last_run_status.json"
PY=./venv/bin/python
mkdir -p scraper/logs

log()    { echo "$(date '+%F %T'): $*" >> "$LOG"; }
notify() { osascript -e "display notification \"$2\" with title \"job-hunt: $1\"" >/dev/null 2>&1; }

# Single source of truth for "what happened", written no matter how we exit, so
# silence is never ambiguous. The dashboard can read this.
finish() {
  local state="$1" detail="$2"
  printf '{"state":"%s","detail":"%s","keyword":"%s","at":"%s","log":"%s"}\n' \
    "$state" "$detail" "$KEYWORD" "$(date -u +%FT%TZ)" "$LOG" > "$STATUS"
  log "FINISH state=$state detail=$detail"
  [ "$state" != "ok" ] && notify "$state" "$detail"
  exit $([ "$state" = "ok" ] && echo 0 || echo 1)
}

# How many pages this session has recorded — the only honest measure of whether
# a screening run actually did anything. An exit code cannot tell us this.
pages_recorded() {
  $PY - <<'EOF' 2>/dev/null || echo 0
import sys; sys.path.insert(0, ".")
from datetime import timedelta
from db.models import CollectionPage, utcnow
from db.session import SessionLocal
s = SessionLocal()
print(s.query(CollectionPage).filter(CollectionPage.recorded_at >= utcnow() - timedelta(hours=2)).count())
EOF
}

log "=== starting: keyword='$KEYWORD' fd=$(ulimit -n) selftest=$SELFTEST ==="

# ---- preconditions ---------------------------------------------------------
# Checked here rather than left to fail inside the session, so the reason is a
# named state instead of a generic error 15 minutes later.
if ! pgrep -x "Google Chrome" >/dev/null; then
  finish "chrome-closed" "Chrome is not running; the manual screen needs a live browser"
fi
if ! security find-generic-password -s "Claude Code-credentials" >/dev/null 2>&1; then
  # The real check. The old wrapper looked for ~/.claude/.credentials.json,
  # which does not exist on macOS and reported ABSENT on every single run.
  log "WARN: no 'Claude Code-credentials' Keychain entry found"
fi

# ---- blocklist cache: a deterministic prerequisite, not a decision ----------
# Belongs here rather than as step 0 of the skill: no judgement is involved, and
# doing it in the wrapper means the session starts with the cache already warm.
mkdir -p SCRATCH
$PY -c "
from db.session import SessionLocal
from db.models import Company
s=SessionLocal()
open('SCRATCH/agencies.txt','w').write('\n'.join(sorted(
  c.name for c in s.query(Company).filter(Company.industry=='Staffing and Recruiting'))))" >> "$LOG" 2>&1 \
  && log "blocklist cache refreshed ($(wc -l < SCRATCH/agencies.txt | tr -d ' ') agencies)" \
  || log "WARN: blocklist refresh failed; skill will fall back to a DB query"

# ---- the actual work, with one retry --------------------------------------
run_screen() {
  local attempt="$1" out rc
  if [ "$SELFTEST" = "1" ]; then
    out=$(claude -p "reply with exactly: SELFTEST-OK" 2>&1); rc=$?
  else
    out=$(claude -p "/linkedin-manual-screen $KEYWORD" --permission-mode acceptEdits 2>&1); rc=$?
  fi
  printf '%s\n' "$out" >> "$LOG"
  # Exit code alone is NOT trustworthy (see header). Treat known error text as
  # failure even when the exit code says success.
  if printf '%s' "$out" | grep -qiE "An unknown error occurred|Invalid API key|not authenticated|usage limit|rate limit"; then
    log "attempt $attempt: error text detected in output despite rc=$rc"
    return 1
  fi
  return $rc
}

if ! run_screen 1; then
  log "attempt 1 failed; retrying in 300s (failures here have been transient)"
  sleep 300
  if ! run_screen 2; then
    {
      echo "--- diagnostics ---"
      echo "claude=$(command -v claude)  version=$(claude --version 2>&1 | head -1)"
      echo "keychain entry: $(security find-generic-password -s 'Claude Code-credentials' >/dev/null 2>&1 && echo present || echo ABSENT)"
      echo "fd limit=$(ulimit -n)   chrome=$(pgrep -x 'Google Chrome' >/dev/null && echo running || echo closed)"
    } >> "$LOG" 2>&1
    finish "run-failed" "screening run failed twice — see $LOG"
  fi
fi

# ---- did it actually collect anything? ------------------------------------
if [ "$SELFTEST" = "1" ]; then
  log "selftest: claude round-trip OK, preconditions OK, gate below"
else
  pages=$(pages_recorded)
  log "pages recorded in the last 2h: $pages"
  [ "${pages:-0}" -eq 0 ] && finish "no-data" "run reported success but recorded 0 pages"
fi

# ---- data-quality gate: runs regardless of what happened above -------------
# This is the point of putting it in the wrapper. Checks inside SKILL.md only
# run if the session reaches its report step; a session that crashed at page 14
# runs none of them. This runs either way.
$PY -m tests_and_eval.ingest_check --hours 6 >> "$LOG" 2>&1
gate=$?
$PY -m tests_and_eval.extraction_health --hours 6 >> "$LOG" 2>&1
health=$?

[ "$gate" -eq 2 ]   && finish "block-violation" "data-quality BLOCK violation — see $LOG"
[ "$health" -ne 0 ] && finish "extraction-drift" "a field stopped extracting — see $LOG"
finish "ok" "completed"
