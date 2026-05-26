"""
AION Terminal Diagnostics
Run before starting server: python -m aion_terminal.scripts.run_diagnostics

Checks DB connectivity, schema completeness, data freshness, agent status,
server reachability, and environment variables. Exit 0 on PASS/WARN; 1 on FAIL.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from aion_terminal.app.config import settings
from aion_terminal.storage.db import bootstrap_schema, get_connection

SCHEMA_PATH = "aion_terminal/storage/schema.sql"
RS_DB_PATH = "aion_terminal/storage/rs_universe.db"

EXPECTED_TABLES = [
    "raw_chain", "computed_levels", "raw_chain_snapshots", "underlying_bars",
    "feature_snapshots", "setup_candidates", "setup_outcomes", "manual_narrative_tags",
    "trade_plans", "trade_plan_outcomes", "agent_memory_summaries",
    "arbitration_snapshots", "adaptive_expectancy", "setup_performance_stats",
    "morning_briefs", "user_trades",
]


def _parse_iso(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _hours_ago(ts, now):
    dt = _parse_iso(ts)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 3600.0


def _status(hours):
    if hours is None:
        return "FAIL"
    if hours < 6.0:
        return "PASS"
    if hours <= 24.0:
        return "WARN"
    return "FAIL"


def main() -> int:
    results: list[tuple[str, str, str]] = []  # (section, status, message)
    fails = 0
    warns = 0

    print("=== AION Terminal Diagnostics ===")
    print(f"Checked at: {datetime.now().isoformat(timespec='seconds')}")
    print()

    # DATABASE
    print("DATABASE")
    db_ok = False
    try:
        conn = get_connection(settings.db_path)
        bootstrap_schema(conn, SCHEMA_PATH)
        print(f"  [PASS] {settings.db_path} — reachable")
        db_ok = True
    except Exception as exc:
        print(f"  [FAIL] {settings.db_path} — {exc}")
        fails += 1
        conn = None

    rs_ok = Path(RS_DB_PATH).exists()
    if rs_ok:
        try:
            rs_conn = sqlite3.connect(RS_DB_PATH)
            rs_conn.close()
            print(f"  [PASS] {RS_DB_PATH} — reachable")
        except Exception as exc:
            print(f"  [WARN] {RS_DB_PATH} — {exc}")
            warns += 1
    else:
        print(f"  [WARN] {RS_DB_PATH} — file missing")
        warns += 1

    if db_ok and conn is not None:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        existing = {r[0] for r in rows}
        missing = [t for t in EXPECTED_TABLES if t not in existing]
        if missing:
            print(f"  [FAIL] Missing tables: {missing}")
            fails += 1
        else:
            print(f"  [PASS] All {len(EXPECTED_TABLES)} expected tables present")
    print()

    # DATA FRESHNESS
    print("DATA FRESHNESS")
    now = datetime.now(timezone.utc)
    if db_ok and conn is not None:
        chain_map = dict(conn.execute(
            "SELECT symbol, MAX(snapshot_ts) FROM raw_chain_snapshots GROUP BY symbol"
        ).fetchall())
        bars_map = dict(conn.execute(
            "SELECT symbol, MAX(bar_ts) FROM underlying_bars "
            "WHERE timeframe IN ('1D','D','daily','1d') GROUP BY symbol"
        ).fetchall())
        feat_map = dict(conn.execute(
            "SELECT symbol, MAX(snapshot_ts) FROM feature_snapshots GROUP BY symbol"
        ).fetchall())

        for sym in settings.watchlist:
            ch = _hours_ago(chain_map.get(sym), now)
            br = _hours_ago(bars_map.get(sym), now)
            ft = _hours_ago(feat_map.get(sym), now)
            statuses = [_status(ch), _status(br), _status(ft)]
            worst = "FAIL" if "FAIL" in statuses else ("WARN" if "WARN" in statuses else "PASS")
            if worst == "FAIL":
                fails += 1
            elif worst == "WARN":
                warns += 1

            def fmt(h):
                return f"{h:.1f}h" if h is not None else "missing"

            print(f"  [{worst}] {sym:5s} chain: {fmt(ch)}  bars: {fmt(br)}  features: {fmt(ft)}")
    print()

    # AGENTS
    print("AGENTS")
    if db_ok and conn is not None:
        row = conn.execute("SELECT MAX(generated_at) FROM morning_briefs").fetchone()
        last = row[0] if row else None
        h = _hours_ago(last, now)
        st = _status(h)
        msg = f"last run {h:.1f}h ago" if h is not None else "no briefs found"
        if st == "FAIL":
            fails += 1
        elif st == "WARN":
            warns += 1
        print(f"  [{st}] Morning brief — {msg}")

    rs_passing = None
    if rs_ok:
        try:
            rs_conn = sqlite3.connect(RS_DB_PATH)
            rs_conn.row_factory = sqlite3.Row
            tables = {r[0] for r in rs_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "rs_scan_results" in tables:
                row = rs_conn.execute("SELECT MAX(scan_ts) AS ts, COUNT(*) AS n FROM rs_scan_results").fetchone()
                if row and row["ts"]:
                    h = _hours_ago(row["ts"], now)
                    st = _status(h)
                    print(f"  [{st}] RS scan — last run {h:.1f}h ago, {row['n']} passing")
                    if st == "WARN":
                        warns += 1
                    elif st == "FAIL":
                        fails += 1
                else:
                    print("  [WARN] RS scan — no results")
                    warns += 1
            else:
                print("  [WARN] RS scan — rs_scan_results table missing")
                warns += 1
            rs_conn.close()
        except Exception as exc:
            print(f"  [WARN] RS scan — {exc}")
            warns += 1

    if db_ok and conn is not None:
        # Arbitration freshness per symbol
        arb_map = dict(conn.execute(
            "SELECT symbol, MAX(generated_at) FROM arbitration_snapshots GROUP BY symbol"
        ).fetchall())
        any_arb = False
        for sym in settings.watchlist:
            ts = arb_map.get(sym)
            if ts:
                any_arb = True
                h = _hours_ago(ts, now)
                st = _status(h)
                if st == "WARN":
                    warns += 1
                elif st == "FAIL":
                    fails += 1
                print(f"  [{st}] Arbitration {sym} — {h:.1f}h ago")
        if not any_arb:
            print("  [WARN] Arbitration — no snapshots for watchlist symbols")
            warns += 1
    print()

    if conn is not None:
        conn.close()

    # SERVER
    print("SERVER")
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:8000/rankings/health", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                mode = "cache-only" if settings.cache_only else "live"
                print(f"  [PASS] localhost:8000 — reachable, {mode} mode")
            else:
                print(f"  [WARN] localhost:8000 — status {resp.status}")
                warns += 1
    except Exception as exc:
        print(f"  [WARN] localhost:8000 — not reachable ({exc})")
        warns += 1
    print()

    # ENVIRONMENT
    print("ENVIRONMENT")
    env_keys = ["MARKETDATA_APP_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
    for key in env_keys:
        val = os.getenv(key, "")
        if val:
            print(f"  [PASS] {key} — set")
        else:
            print(f"  [WARN] {key} — not set")
            warns += 1
    cache_only = "true" if settings.cache_only else "false"
    print(f"  [PASS] AION_CACHE_ONLY — {cache_only}")
    print()

    print(f"=== {fails} FAIL, {warns} WARN ===")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
