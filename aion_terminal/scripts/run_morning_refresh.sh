#!/bin/bash
# AION Terminal — Morning Refresh
# Runs weekdays at 9:00am ET via cron
# Sequence: backfill bars -> snapshot -> cache invalidate -> morning brief
# On Fridays: also writes weekly JSON rollup
#
# To install cron job (runs at 9:00am ET, weekdays only):
#   crontab -e
# Add this line:
#   0 9 * * 1-5 /Users/khaliwilliams/limitless-orb/aion_terminal/scripts/run_morning_refresh.sh

set -e

REPO_DIR="/Users/khaliwilliams/limitless-orb"
VENV="$REPO_DIR/venv/bin/activate"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/morning_$(date +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"

cd "$REPO_DIR"
source "$VENV"
export PYTHONPATH="$REPO_DIR"

echo "=== AION Morning Refresh $(date) ===" | tee -a "$LOG_FILE"

# Build symbol list from .env WATCHLIST
SYMBOLS=$(python -c "
import os
from dotenv import load_dotenv
load_dotenv()
w = os.getenv('WATCHLIST','')
symbols = [s.strip().upper() for s in w.split(',') if s.strip()]
print(','.join(symbols))
")
echo "Watchlist: $SYMBOLS" | tee -a "$LOG_FILE"

echo "[1/4] Refreshing bars..." | tee -a "$LOG_FILE"
python -m aion_terminal.scripts.run_backfill \
  --symbols "$SYMBOLS" \
  --days 90 \
  --timeframes 1d \
  --verbose >> "$LOG_FILE" 2>&1

echo "[2/4] Running chain snapshot..." | tee -a "$LOG_FILE"
python -m aion_terminal.scripts.run_daily_snapshot \
  --symbols "$SYMBOLS" \
  --max-symbols 20 \
  --sleep-seconds 6 \
  --skip-refresh-if-recent-minutes 0 \
  --verbose >> "$LOG_FILE" 2>&1

echo "[3/4] Invalidating cache..." | tee -a "$LOG_FILE"
curl -s -X POST http://localhost:8000/cache/invalidate >> "$LOG_FILE" 2>&1 || \
  echo "Cache invalidate skipped (server may not be running)" | tee -a "$LOG_FILE"

echo "[4/4] Running morning brief..." | tee -a "$LOG_FILE"
python -m aion_terminal.scripts.run_morning_brief >> "$LOG_FILE" 2>&1

echo "=== Refresh complete $(date) ===" | tee -a "$LOG_FILE"
