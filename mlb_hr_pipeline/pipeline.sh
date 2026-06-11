#!/usr/bin/env bash
# Daily orchestrator. Runs in order: score yesterday -> ingest today ->
# predict today. Backtest + fit are run weekly (Sundays) since they're heavy.
#
# Usage: ./pipeline.sh [YYYY-MM-DD]   (defaults to today's date)
set -euo pipefail
cd "$(dirname "$0")"

DATE="${1:-$(date -u +%F)}"
YESTERDAY=$(python -c "import datetime; print((datetime.date.fromisoformat('${DATE}') - datetime.timedelta(days=1)).isoformat())")
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
  run python score.py "$YESTERDAY" || true
else
  echo "no predictions_${YESTERDAY}.json — skipping scoring" | tee -a "$LOG"
fi

# 2. Weekly refit on Sundays (heavy)
if [[ "$DOW" == "7" ]]; then
  run python backtest.py || true
  run python fit_model.py || true
fi

# 3. Today's ingest + predictions
run python ingest_live.py "$DATE"
run python predict_today.py "$DATE"

# Best-effort: tell the local API to reload from disk. Never fails the pipeline.
curl -s -X POST "${API_URL:-http://localhost:8000}/api/reload" >/dev/null 2>&1 || true

echo "=== done $(date -u +%FT%TZ) ===" | tee -a "$LOG"
