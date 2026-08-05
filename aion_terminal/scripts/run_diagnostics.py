"""
AION Terminal Diagnostics
Run before starting server: python -m aion_terminal.scripts.run_diagnostics

Checks DB connectivity, schema completeness, data freshness, agent status,
server reachability, and environment variables. Exit 0 on PASS/WARN; 1 on FAIL.
"""

from __future__ import annotations

import json
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
                # Informational, not a warning. rs_scan_results is created by
                # run_rs_scan in its own database; its absence before the first
                # scan is the expected state, and a permanent WARN here trains
                # the operator to ignore warnings generally (P2-10).
                print("  [INFO] RS scan — no scan has run yet (rs_scan_results not created)")
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

    # SEMANTIC CHECKS
    # Structural checks (tables exist, files are fresh) passed while the system
    # produced nothing usable, which trained the operator to read PASS as
    # "working". These assert on output, not plumbing.
    s_fails, s_warns = _semantic_checks()
    fails += s_fails
    warns += s_warns

    print(f"=== {fails} FAIL, {warns} WARN ===")
    return 0 if fails == 0 else 1


def _semantic_checks() -> tuple[int, int]:
    """Assert the system is producing usable output, not merely running."""
    from aion_terminal.arbitration.arbiter import (
        DEGRADED_SETUP_CLASS,
        persistence_failure_count,
    )

    fails = 0
    warns = 0
    print("SEMANTIC OUTPUT")

    try:
        conn = get_connection(settings.db_path)
    except Exception as exc:
        print(f"  [FAIL] cannot open DB for semantic checks ({exc})")
        print()
        return 1, 0

    def count(sql: str, params: tuple = ()) -> int:
        try:
            return conn.execute(sql, params).fetchone()[0]
        except Exception:
            return -1

    try:
        # 1. Are real evaluators producing setups, or only the degraded fallback?
        total_setups = count("SELECT COUNT(*) FROM setup_candidates")
        degraded = count("SELECT COUNT(*) FROM setup_candidates WHERE setup_class = ?", (DEGRADED_SETUP_CLASS,))
        if total_setups <= 0:
            print("  [FAIL] setup_candidates — empty; nothing is being produced")
            fails += 1
        else:
            real = total_setups - max(0, degraded)
            if real == 0:
                print(f"  [FAIL] setup_candidates — all {total_setups} are the degraded fallback")
                fails += 1
            else:
                print(f"  [PASS] setup_candidates — {real}/{total_setups} from real evaluators")

        # 2. Does the arbiter reach more than one decision?
        rows = []
        try:
            rows = conn.execute(
                "SELECT arb_decision, COUNT(*) FROM arbitration_snapshots GROUP BY arb_decision"
            ).fetchall()
        except Exception:
            pass
        if not rows:
            print("  [FAIL] arbitration_snapshots — empty; the reasoning layer never ran")
            fails += 1
        else:
            dist = {r[0]: r[1] for r in rows}
            total = sum(dist.values())
            dominant, dominant_n = max(dist.items(), key=lambda kv: kv[1])
            summary = ", ".join(f"{k}={v}" for k, v in sorted(dist.items(), key=lambda kv: -kv[1]))
            if len(dist) == 1:
                print(f"  [FAIL] arbitration decisions — every row is '{dominant}'; not deciding, following a rule")
                fails += 1
            elif dominant_n / total > 0.95:
                print(f"  [WARN] arbitration decisions — {dominant_n/total:.0%} are '{dominant}' ({summary})")
                warns += 1
            else:
                print(f"  [PASS] arbitration decisions — {summary}")

        # 3. Which agreement channels carry information? A channel with one
        #    distinct value never influenced a decision.
        try:
            blobs = [r[0] for r in conn.execute(
                "SELECT agreement_json FROM arbitration_snapshots "
                "WHERE agreement_json IS NOT NULL ORDER BY generated_at DESC LIMIT 500"
            )]
        except Exception:
            blobs = []
        if blobs:
            seen: dict[str, set] = {}
            for b in blobs:
                try:
                    d = json.loads(b)
                except Exception:
                    continue
                for k, v in (d or {}).items():
                    if isinstance(v, (int, float)):
                        seen.setdefault(k, set()).add(round(float(v), 6))
            dead = sorted(k for k, v in seen.items() if len(v) == 1)
            live = sorted(k for k, v in seen.items() if len(v) > 1)
            if dead:
                print(f"  [WARN] agreement channels constant (carry no information): {', '.join(dead)}")
                warns += 1
            if live:
                print(f"  [PASS] agreement channels live: {', '.join(live)}")

        # 4. Outcome loop — the gate to trusting any of this.
        outcomes = count("SELECT COUNT(*) FROM setup_outcomes")
        if outcomes <= 0:
            print("  [FAIL] setup_outcomes — empty; nothing has been scored against what price did")
            fails += 1
        else:
            print(f"  [PASS] setup_outcomes — {outcomes} scored")
        expectancy = count("SELECT COUNT(*) FROM adaptive_expectancy")
        if expectancy <= 0:
            print("  [WARN] adaptive_expectancy — empty; sizing is unavailable and reported as unsized")
            warns += 1
        else:
            print(f"  [PASS] adaptive_expectancy — {expectancy} scopes")

        # 5. Dealer-map consistency: the arbiter must read the same combined map
        #    the UI renders, for the same (symbol, snapshot_ts).
        symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM feature_snapshots")]
        missing = [
            s for s in symbols
            if not count("SELECT COUNT(*) FROM feature_snapshots WHERE symbol=? AND expiry='combined'", (s,))
        ]
        if not symbols:
            print("  [WARN] feature_snapshots — empty; no dealer map to check")
            warns += 1
        elif missing:
            print(f"  [FAIL] dealer map — {len(missing)}/{len(symbols)} symbols have no combined snapshot")
            fails += 1
        else:
            print(f"  [PASS] dealer map — combined snapshot present for all {len(symbols)} symbols")

        # 6. Reasoning records that failed to persist (P2-4).
        pf = persistence_failure_count()
        if pf:
            print(f"  [FAIL] arbitration persistence — {pf} rows failed to write this process")
            fails += 1
    finally:
        conn.close()

    print()
    return fails, warns


if __name__ == "__main__":
    raise SystemExit(main())
