"""Phase 0 remediation instrumentation.

Measures the four numbers that tell us whether a remediation stage actually
changed system behaviour, rather than merely changing code:

  1. Setup production      — are the real evaluators firing, or only the
                             degraded ``technical_dealer_watch`` fallback?
  2. Decision distribution — what does the arbiter actually decide, and can
                             it reach ``trade`` at all?
  3. Agreement channels    — which of the six scoring channels carry
                             information, and which are pinned constants?
  4. Dealer-map coherence  — does a combined-expiry snapshot exist for the
                             arbiter to read (P0-1), and do UI and arbiter
                             see the same map?

Run it before and after each stage and diff the output. A stage that claims
to fix a channel but leaves its ``distinct`` count at 1 did not land.

Because a pipeline run *appends* to the existing tables, an unfiltered
re-run blends pre- and post-fix rows and washes out the very signal we are
measuring. Pass ``--since`` with a timestamp taken immediately before the
run to isolate only the rows the new code wrote.

Usage:
    python -m aion_terminal.scripts.run_instrumentation [--db PATH] [--json]
    python -m aion_terminal.scripts.run_instrumentation --since 2026-08-05T00:00:00
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import Counter
from typing import Any

CHANNELS = ("technical", "dealer", "contracts", "macro", "memory", "expectancy")
TRADE_THRESHOLD = 0.70
DEGRADED_SETUP_CLASS = "technical_dealer_watch"


def _where(column: str, since: str | None) -> tuple[str, tuple]:
    """Build an optional ``since`` filter for a timestamp column."""
    if not since:
        return "", ()
    return f" WHERE {column} >= ?", (since,)


def _and(column: str, since: str | None) -> tuple[str, tuple]:
    """Same filter, for queries that already have a WHERE clause."""
    if not since:
        return "", ()
    return f" AND {column} >= ?", (since,)


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    out: dict[str, int] = {}
    for name in sorted(names):
        out[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    return out


def measure_setups(conn: sqlite3.Connection, since: str | None = None) -> dict[str, Any]:
    """(1) Are the real evaluators firing, or only the degraded fallback?"""
    w, p = _where("as_of_ts", since)
    by_class = dict(conn.execute(f"SELECT setup_class, COUNT(*) FROM setup_candidates{w} GROUP BY setup_class", p))
    total = sum(by_class.values())
    degraded = by_class.get(DEGRADED_SETUP_CLASS, 0)
    return {
        "total": total,
        "by_class": by_class,
        "degraded": degraded,
        "non_degraded": total - degraded,
        "degraded_pct": (degraded / total * 100.0) if total else None,
    }


def measure_decisions(conn: sqlite3.Connection, since: str | None = None) -> dict[str, Any]:
    """(2) What does the arbiter decide, and can it reach ``trade``?"""
    w, p = _where("generated_at", since)
    a, ap = _and("generated_at", since)
    total = conn.execute(f"SELECT COUNT(*) FROM arbitration_snapshots{w}", p).fetchone()[0]
    if not total:
        return {"total": 0}

    def group(col: str) -> dict[str, int]:
        return dict(conn.execute(f"SELECT {col}, COUNT(*) FROM arbitration_snapshots{w} GROUP BY {col}", p))

    with_trigger = conn.execute(
        f"SELECT COUNT(*) FROM arbitration_snapshots WHERE required_trigger_json IS NOT NULL{a}", ap
    ).fetchone()[0]

    # The mechanical path: bias -> trigger presence -> decision. If every
    # directional row lands on a single decision, the arbiter is not deciding,
    # it is following a fixed rule.
    mechanism = Counter()
    for bias, trig, decision in conn.execute(
        f"""SELECT final_bias,
                   CASE WHEN required_trigger_json IS NULL THEN 'no_trigger' ELSE 'has_trigger' END,
                   arb_decision
            FROM arbitration_snapshots{w}""",
        p,
    ):
        mechanism[(bias, trig, decision)] += 1

    conflicts: Counter[str] = Counter()
    for (raw,) in conn.execute(
        f"SELECT conflicts_json FROM arbitration_snapshots WHERE conflicts_json IS NOT NULL{a}", ap
    ):
        try:
            for c in json.loads(raw):
                conflicts[c] += 1
        except (TypeError, ValueError):
            continue

    return {
        "total": total,
        "by_decision": group("arb_decision"),
        "by_bias": group("final_bias"),
        "by_bucket": group("confidence_bucket"),
        "with_required_trigger": with_trigger,
        "with_required_trigger_pct": with_trigger / total * 100.0,
        "trade_count": group("arb_decision").get("trade", 0),
        "mechanism": {f"{b}|{t}|{d}": n for (b, t, d), n in mechanism.most_common()},
        "conflicts": dict(conflicts.most_common()),
    }


def measure_channels(conn: sqlite3.Connection, since: str | None = None) -> dict[str, Any]:
    """(3) Which scoring channels carry information, and which are constants?

    ``distinct == 1`` means the channel contributed nothing to any decision
    ever made — it is a constant wearing a score's clothing.
    """
    a, ap = _and("generated_at", since)
    rows: list[dict] = []
    for (raw,) in conn.execute(
        f"SELECT agreement_json FROM arbitration_snapshots WHERE agreement_json IS NOT NULL{a}", ap
    ):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                rows.append(parsed)
        except (TypeError, ValueError):
            continue

    if not rows:
        return {"n": 0}

    per_channel: dict[str, Any] = {}
    for ch in CHANNELS:
        vals = [float(r[ch]) for r in rows if isinstance(r.get(ch), (int, float))]
        if not vals:
            per_channel[ch] = {"present": False}
            continue
        per_channel[ch] = {
            "present": True,
            "min": min(vals),
            "mean": statistics.mean(vals),
            "max": max(vals),
            "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
            "distinct": len(set(vals)),
            "is_constant": len(set(vals)) == 1,
        }

    avgs = [
        sum(float(r[ch]) for ch in CHANNELS if isinstance(r.get(ch), (int, float)))
        / max(1, sum(1 for ch in CHANNELS if isinstance(r.get(ch), (int, float))))
        for r in rows
    ]

    # A channel pinned at a constant caps the attainable average. If the
    # resulting ceiling sits below TRADE_THRESHOLD, `trade` is not merely
    # rare — it is arithmetically unreachable.
    live = [c for c in CHANNELS if per_channel.get(c, {}).get("present") and not per_channel[c]["is_constant"]]
    const = [c for c in CHANNELS if per_channel.get(c, {}).get("present") and per_channel[c]["is_constant"]]
    ceiling = None
    if live or const:
        ceiling = (
            sum(per_channel[c]["max"] for c in live) + sum(per_channel[c]["mean"] for c in const)
        ) / (len(live) + len(const))

    return {
        "n": len(rows),
        "channels": per_channel,
        "live_channels": live,
        "constant_channels": const,
        "avg": {
            "min": min(avgs),
            "mean": statistics.mean(avgs),
            "max": max(avgs),
            "stdev": statistics.stdev(avgs) if len(avgs) > 1 else 0.0,
        },
        "attainable_ceiling": ceiling,
        "trade_threshold": TRADE_THRESHOLD,
        "trade_reachable": bool(ceiling is not None and ceiling >= TRADE_THRESHOLD),
        "at_or_above_threshold": sum(1 for a in avgs if a >= TRADE_THRESHOLD),
    }


def measure_dealer_map(conn: sqlite3.Connection, since: str | None = None) -> dict[str, Any]:
    """(4) Does the arbiter read the same dealer map the UI renders? (P0-1)"""
    w, p = _where("snapshot_ts", since)
    a, ap = _and("snapshot_ts", since)
    by_expiry = dict(
        conn.execute(f"SELECT expiry, COUNT(*) FROM feature_snapshots{w} GROUP BY expiry ORDER BY COUNT(*) DESC", p)
    )
    combined = by_expiry.get("combined", 0)

    # Per symbol: is there a combined snapshot at all?
    symbols = [r[0] for r in conn.execute(f"SELECT DISTINCT symbol FROM feature_snapshots{w}", p)]
    with_combined = [
        s
        for s in symbols
        if conn.execute(
            f"SELECT COUNT(*) FROM feature_snapshots WHERE symbol=? AND expiry='combined'{a}", (s, *ap)
        ).fetchone()[0]
    ]

    return {
        "total_snapshots": sum(by_expiry.values()),
        "distinct_expiries": len(by_expiry),
        "combined_rows": combined,
        "symbols": len(symbols),
        "symbols_with_combined": len(with_combined),
        "symbols_missing_combined": sorted(set(symbols) - set(with_combined)),
        "consistent": bool(symbols) and len(with_combined) == len(symbols),
    }


def collect(db_path: str, since: str | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            "db": db_path,
            "since": since,
            "tables": _table_counts(conn),
            "setups": measure_setups(conn, since),
            "decisions": measure_decisions(conn, since),
            "channels": measure_channels(conn, since),
            "dealer_map": measure_dealer_map(conn, since),
        }
    finally:
        conn.close()


def _pct(n: int, d: int) -> str:
    return f"{n / d * 100:.1f}%" if d else "n/a"


def render(report: dict[str, Any]) -> str:
    out: list[str] = []
    add = out.append

    add("=== AION PHASE 0 INSTRUMENTATION ===")
    add(f"db: {report['db']}")
    add(f"since: {report.get('since') or '(all rows)'}")
    add("")

    s = report["setups"]
    add("--- 1. SETUP PRODUCTION ---")
    if not s["total"]:
        add("  no setup_candidates rows")
    else:
        for cls, n in sorted(s["by_class"].items(), key=lambda kv: -kv[1]):
            tag = "  (DEGRADED FALLBACK)" if cls == DEGRADED_SETUP_CLASS else ""
            add(f"  {n:>6}  {cls}{tag}")
        add(f"  non-degraded: {s['non_degraded']}/{s['total']} ({_pct(s['non_degraded'], s['total'])})")
    add("")

    d = report["decisions"]
    add("--- 2. ARBITRATION DECISIONS ---")
    if not d.get("total"):
        add("  no arbitration_snapshots rows")
    else:
        for k, n in sorted(d["by_decision"].items(), key=lambda kv: -kv[1]):
            add(f"  {n:>6}  {k}")
        add(f"  trade decisions: {d['trade_count']}")
        add(f"  required_trigger present: {d['with_required_trigger']}/{d['total']} ({d['with_required_trigger_pct']:.1f}%)")
        add("  bias:")
        for k, n in sorted(d["by_bias"].items(), key=lambda kv: -kv[1]):
            add(f"    {n:>6}  {k}")
        add("  mechanism (bias | trigger | decision):")
        for k, n in list(d["mechanism"].items())[:8]:
            add(f"    {n:>6}  {k}")
        add("  conflicts:")
        for k, n in d["conflicts"].items():
            add(f"    {n:>6} ({_pct(n, d['total']):>6})  {k}")
    add("")

    c = report["channels"]
    add("--- 3. AGREEMENT CHANNELS ---")
    if not c.get("n"):
        add("  no agreement_json rows")
    else:
        add(f"  n = {c['n']}")
        add(f"  {'channel':<12}{'min':>7}{'mean':>7}{'max':>7}{'stdev':>7}{'distinct':>10}   status")
        for ch in CHANNELS:
            st = c["channels"].get(ch, {})
            if not st.get("present"):
                add(f"  {ch:<12}{'—':>7}{'—':>7}{'—':>7}{'—':>7}{'—':>10}   MISSING")
                continue
            status = "CONSTANT (dead)" if st["is_constant"] else "live"
            add(
                f"  {ch:<12}{st['min']:>7.3f}{st['mean']:>7.3f}{st['max']:>7.3f}"
                f"{st['stdev']:>7.3f}{st['distinct']:>10}   {status}"
            )
        a = c["avg"]
        add(f"  {'AVG':<12}{a['min']:>7.3f}{a['mean']:>7.3f}{a['max']:>7.3f}{a['stdev']:>7.3f}")
        add("")
        if c["attainable_ceiling"] is not None:
            verdict = "REACHABLE" if c["trade_reachable"] else "UNREACHABLE"
            add(f"  attainable ceiling : {c['attainable_ceiling']:.4f}")
            add(f"  trade threshold    : {c['trade_threshold']:.4f}  -> {verdict}")
        add(f"  rows at/above threshold: {c['at_or_above_threshold']}/{c['n']}")
    add("")

    m = report["dealer_map"]
    add("--- 4. DEALER MAP COHERENCE (P0-1) ---")
    add(f"  feature_snapshots: {m['total_snapshots']} rows across {m['distinct_expiries']} expiry values")
    add(f"  combined-expiry rows: {m['combined_rows']}")
    add(f"  symbols with combined snapshot: {m['symbols_with_combined']}/{m['symbols']}")
    if m["symbols_missing_combined"]:
        missing = ", ".join(m["symbols_missing_combined"][:12])
        add(f"  MISSING combined: {missing}")
    add(f"  consistent: {m['consistent']}")

    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 0 remediation instrumentation")
    parser.add_argument("--db", default="options_terminal.db", help="path to the SQLite DB")
    parser.add_argument("--json", action="store_true", help="emit raw JSON instead of the rendered report")
    parser.add_argument(
        "--since",
        default=None,
        help="ISO timestamp; count only rows written at or after it. Take it "
        "immediately before a pipeline run to isolate that run's output.",
    )
    args = parser.parse_args()

    report = collect(args.db, args.since)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))


if __name__ == "__main__":
    main()
