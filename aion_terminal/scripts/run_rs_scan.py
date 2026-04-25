from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone

from aion_terminal.data.rs_engine import SCAN_INTERVAL_DAYS, load_universe, run_rs_scan
from aion_terminal.signals.leadership import detect_leadership_events
from aion_terminal.storage.rs_repositories import (
    bootstrap_rs_schema,
    expire_stale_candidates,
    get_rs_connection,
    log_scan_run,
    query_recent_scan_runs,
    upsert_rs_candidate,
)
from aion_terminal.utils.logging_utils import configure_logging
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run relative strength screener scan")
    parser.add_argument("--force", action="store_true", help="Run regardless of last run time")
    parser.add_argument("--dry-run", action="store_true", help="Run scan without DB upserts")
    return parser.parse_args()


def _recent_enough(last_run: str) -> bool:
    try:
        last = datetime.fromisoformat(last_run)
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last < timedelta(days=SCAN_INTERVAL_DAYS)


def main() -> int:
    configure_logging()
    args = _parse_args()

    conn = get_rs_connection()
    bootstrap_rs_schema(conn)

    started = time.time()
    now = utc_now_iso()

    try:
        recent_runs = query_recent_scan_runs(conn, limit=1)
        if recent_runs and not args.force and _recent_enough(recent_runs[0]["run_at"]):
            print("=== RS SCAN RESULTS ===")
            print(f"Run at: {now}")
            print("Skipped: last scan is newer than SCAN_INTERVAL_DAYS. Use --force to override.")
            return 0

        universe = load_universe()
        passing = run_rs_scan(universe)
        events = detect_leadership_events(passing)

        duration = time.time() - started

        if not args.dry_run:
            for feat in passing:
                upsert_rs_candidate(conn, feat)
            expire_stale_candidates(conn)

        log_scan_run(
            conn,
            tickers_scanned=len(universe),
            tickers_passing=len(passing),
            duration_seconds=duration,
            errors="",
        )

        event_1 = sum(1 for e in events if e.event_type == "rs_new_high_before_price")
        event_2 = sum(1 for e in events if e.event_type == "rs_new_high")
        event_3 = sum(1 for e in events if e.event_type == "rs_emerging")

        print("=== RS SCAN RESULTS ===")
        print(f"Run at: {now}")
        print(f"Universe loaded: {len(universe)} tickers")
        print(f"Passing screen (3 of 5 conditions): {len(passing)}")
        print(f"  RS new high before price: {event_1}")
        print(f"  RS new high: {event_2}")
        print(f"  RS emerging: {event_3}")
        print("Top 10 candidates:")
        for i, feat in enumerate(passing[:10], start=1):
            print(
                f"  {i}. {feat.ticker} | RS%ile: {feat.rs_percentile:.1f} | "
                f"Conditions: {feat.conditions_met}/5 | Close: ${feat.close:.2f} | "
                f"1Y: {feat.return_1y_pct:.1f}%"
            )
        print(f"Scan duration: {duration:.1f}s")
        return 0
    except Exception as exc:
        duration = time.time() - started
        logger.exception("RS scan failed")
        log_scan_run(conn, 0, 0, duration_seconds=duration, errors=str(exc))
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
