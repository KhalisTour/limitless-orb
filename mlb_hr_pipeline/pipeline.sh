#!/usr/bin/env bash
# Daily orchestrator. Runs in order: score yesterday -> recalibrate ->
# ingest today -> predict today. Backtest + the three fits run weekly (Sundays)
# since they're heavy.
#
# Usage: ./pipeline.sh [YYYY-MM-DD]   (defaults to today's date)
set -euo pipefail
cd "$(dirname "$0")"

# cron runs with a minimal PATH that often has no `python`. schedule_runs.py
# passes the interpreter it is itself running under; fall back to whatever is
# on PATH for interactive use.
PYTHON="${PYTHON:-python}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python3

DATE="${1:-$(date -u +%F)}"
YESTERDAY=$("$PYTHON" -c "import datetime; print((datetime.date.fromisoformat('${DATE}') - datetime.timedelta(days=1)).isoformat())")
DOW=$(date -u +%u)   # 1=Mon ... 7=Sun
mkdir -p logs data models_out
LOG="logs/pipeline_${DATE}.log"

run() {
  echo "=== $(date -u +%FT%TZ)  $* ===" | tee -a "$LOG"
  if ! "$@" 2>&1 | tee -a "$LOG"; then
    echo "FAILED: $*  (see $LOG)" | tee -a "$LOG"
    return 1
  fi
}

echo "Pipeline date=$DATE yesterday=$YESTERDAY dow=$DOW" | tee -a "$LOG"

# 1. Score yesterday's predictions if we have them (skip silently if not)
if [[ -f "data/predictions_${YESTERDAY}.json" ]]; then
  run "$PYTHON" score.py "$YESTERDAY" || true
else
  echo "no predictions_${YESTERDAY}.json — skipping scoring" | tee -a "$LOG"
fi

# 2. Re-estimate the calibration constants from everything scored so far.
# Cheap, and calibrate.py refuses to write a calibration that scores worse than
# a constant league-rate forecast, so a bad day cannot poison the model.
run "$PYTHON" calibrate.py || true

# 3. Weekly refit on Sundays (heavy). All three targets, not just HR — only
# fit_model.py used to run here, so the XBH and hit coefficients were whatever
# the last manual run left behind while the HR ones moved underneath them.
if [[ "$DOW" == "7" ]]; then
  run "$PYTHON" backtest.py || true
  run "$PYTHON" fit_model.py || true
  run "$PYTHON" fit_model_xbh.py || true
  run "$PYTHON" fit_model_hit.py || true
fi

# 4. Today's ingest + predictions
run "$PYTHON" ingest_live.py "$DATE"
run "$PYTHON" predict_today.py "$DATE"

# 5. Free HR-prop odds via The-Odds-API (best-effort; never fails the pipeline).
run "$PYTHON" fetch_odds.py "$DATE" || true

# Best-effort: tell the local API to reload from disk. Never fails the pipeline.
curl -s -X POST "${API_URL:-http://localhost:8000}/api/reload" >/dev/null 2>&1 || true

echo "=== done $(date -u +%FT%TZ) ===" | tee -a "$LOG"
