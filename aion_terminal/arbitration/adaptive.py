from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from aion_terminal.arbitration.schemas import AgreementMatrix
from aion_terminal.utils.time_utils import utc_now_iso


_CRITICAL_CONFLICTS = {
    "no_liquid_contracts",
    "macro_risk_not_supportive",
}


def _row_to_dict(row) -> dict | None:
    return dict(row) if row else None


def _safe_parse(blob: str | None) -> dict:
    if not blob:
        return {}
    try:
        out = json.loads(blob)
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def compute_expectancy_modifier(
    conn: sqlite3.Connection,
    setup_class: str,
    regime: str,
    direction: str,
    dte_bucket: str,
    moneyness: str,
) -> float:
    """Return modifier in [-1.0, 1.0]; 0.0 if no history."""
    candidate_scopes = [
        f"setup:{setup_class}|regime:{regime}|dir:{direction}|dte:{dte_bucket}|m:{moneyness}",
        f"setup:{setup_class}|regime:{regime}|dir:{direction}",
        f"setup:{setup_class}|dir:{direction}",
        f"setup:{setup_class}",
    ]
    for scope in candidate_scopes:
        row = conn.execute(
            "SELECT expectancy_modifier FROM adaptive_expectancy WHERE scope = ?",
            (scope,),
        ).fetchone()
        if row and row["expectancy_modifier"] is not None:
            try:
                return max(-1.0, min(1.0, float(row["expectancy_modifier"])))
            except (TypeError, ValueError):
                continue
    return 0.0


def compute_memory_penalty(conn: sqlite3.Connection, symbol: str, setup_class: str) -> dict:
    """Inspect agent_memory_summaries; return aggregate penalty 0..0.5."""
    scopes = ["global", f"symbol:{symbol}", f"setup:{setup_class}"]
    total = 0.0
    reasons: list[str] = []
    seen_reasons: set[str] = set()
    for scope in scopes:
        row = conn.execute(
            "SELECT summary_json FROM agent_memory_summaries WHERE scope = ? ORDER BY updated_at DESC LIMIT 1",
            (scope,),
        ).fetchone()
        if not row:
            continue
        summary = _safe_parse(row["summary_json"])
        patterns = summary.get("penalty_patterns") or summary.get("patterns") or []
        if isinstance(patterns, dict):
            patterns = list(patterns.keys())
        for p in patterns:
            ps = str(p).lower()
            penalty = 0.0
            if "early_exit" in ps or "premature_exit" in ps:
                penalty = 0.10
            elif "far_otm" in ps:
                penalty = 0.15
            elif "oversized" in ps or "over_sized" in ps:
                penalty = 0.10
            if penalty > 0 and ps not in seen_reasons:
                total += penalty
                reasons.append(ps)
                seen_reasons.add(ps)
    return {"total_penalty": min(0.5, total), "reasons": reasons}


def determine_contract_role(
    agreement: AgreementMatrix,
    conflicts: list[str],
    expectancy_modifier: float,
    memory_penalty: float,
) -> str:
    avg = agreement.average()
    critical = [c for c in conflicts if c in _CRITICAL_CONFLICTS]
    if avg < 0.35 or "no_liquid_contracts" in conflicts:
        return "no_trade"
    if critical or memory_penalty > 0.3:
        return "safer_only"
    if avg >= 0.70 and expectancy_modifier >= 0.0 and not critical:
        return "convex"
    if avg < 0.5:
        return "safer_only"
    return "balanced"


def compute_sizing_modifier(
    confidence: float,
    agreement_avg: float,
    memory_penalty: float,
    regime: str,
    has_acceptance: bool,
) -> float:
    base = 1.0
    base *= max(0.0, min(1.0, confidence or 0.0))
    base *= max(0.0, min(1.0, agreement_avg or 0.0))
    base *= max(0.0, 1.0 - (memory_penalty or 0.0))
    if not has_acceptance:
        base *= 0.5
    if (regime or "").lower() in ("range", "ranging"):
        base *= 0.7
    return max(0.1, min(1.0, base))


def _bucket_dte(dte: Any) -> str:
    try:
        d = int(dte)
    except (TypeError, ValueError):
        return "unknown"
    if d <= 3:
        return "0-3"
    if d <= 7:
        return "4-7"
    if d <= 14:
        return "8-14"
    if d <= 30:
        return "15-30"
    return "30+"


def _modifier_from_pnl(avg_pnl_pct: float) -> float:
    # Map ±50% avg pnl → ±1.0 modifier
    return max(-1.0, min(1.0, (avg_pnl_pct or 0.0) / 50.0))


def rebuild_expectancy_summaries(conn: sqlite3.Connection) -> int:
    """Recompute adaptive_expectancy from setup_outcomes JOIN setup_candidates."""
    rows = conn.execute(
        """
        SELECT sc.setup_class AS setup_class,
               sc.direction AS direction,
               so.pnl_pct AS pnl_pct,
               so.max_favorable_excursion AS mfe,
               so.max_adverse_excursion AS mae,
               so.hold_minutes AS hold_min,
               so.is_winner AS is_winner
        FROM setup_outcomes so
        JOIN setup_candidates sc ON sc.candidate_id = so.candidate_id
        """
    ).fetchall()

    # Group by setup_class and (setup_class, direction)
    groups: dict[str, list] = {}
    for r in rows:
        sc = r["setup_class"]
        direction = r["direction"] or "unknown"
        if not sc:
            continue
        for scope in (f"setup:{sc}", f"setup:{sc}|dir:{direction}"):
            groups.setdefault(scope, []).append(r)

    updated = 0
    now = utc_now_iso()
    for scope, items in groups.items():
        if len(items) < 5:
            continue
        wins = sum(1 for x in items if x["is_winner"])
        win_rate = wins / len(items)
        pnls = [x["pnl_pct"] for x in items if x["pnl_pct"] is not None]
        mfes = [x["mfe"] for x in items if x["mfe"] is not None]
        maes = [x["mae"] for x in items if x["mae"] is not None]
        holds = [x["hold_min"] for x in items if x["hold_min"] is not None]
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        avg_mfe = sum(mfes) / len(mfes) if mfes else 0.0
        avg_mae = sum(maes) / len(maes) if maes else 0.0
        avg_hold = sum(holds) / len(holds) if holds else 0.0
        modifier = _modifier_from_pnl(avg_pnl)
        existing = conn.execute(
            "SELECT exp_id FROM adaptive_expectancy WHERE scope = ?",
            (scope,),
        ).fetchone()
        exp_id = existing["exp_id"] if existing else str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO adaptive_expectancy (
                exp_id, updated_at, scope, sample_count, win_rate, avg_pnl_pct,
                avg_mfe_pct, avg_mae_pct, avg_hold_minutes, expectancy_modifier, raw_stats_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                updated_at = excluded.updated_at,
                sample_count = excluded.sample_count,
                win_rate = excluded.win_rate,
                avg_pnl_pct = excluded.avg_pnl_pct,
                avg_mfe_pct = excluded.avg_mfe_pct,
                avg_mae_pct = excluded.avg_mae_pct,
                avg_hold_minutes = excluded.avg_hold_minutes,
                expectancy_modifier = excluded.expectancy_modifier,
                raw_stats_json = excluded.raw_stats_json
            """,
            (
                exp_id,
                now,
                scope,
                len(items),
                win_rate,
                avg_pnl,
                avg_mfe,
                avg_mae,
                avg_hold,
                modifier,
                json.dumps({"n": len(items)}),
            ),
        )
        updated += 1
    conn.commit()
    return updated


def rebuild_setup_performance_stats(conn: sqlite3.Connection) -> int:
    """Aggregate per setup_class+direction (cross-symbol)."""
    rows = conn.execute(
        """
        SELECT sc.setup_class AS setup_class,
               sc.direction AS direction,
               sc.symbol AS symbol,
               so.pnl_pct AS pnl_pct,
               so.is_winner AS is_winner
        FROM setup_outcomes so
        JOIN setup_candidates sc ON sc.candidate_id = so.candidate_id
        """
    ).fetchall()

    groups: dict[tuple, list] = {}
    for r in rows:
        key = (None, r["setup_class"] or "unknown", r["direction"] or "unknown", None, None, None)
        groups.setdefault(key, []).append(r)

    updated = 0
    now = utc_now_iso()
    for key, items in groups.items():
        if len(items) < 5:
            continue
        symbol, setup_class, direction, regime, moneyness, dte_bucket = key
        wins = sum(1 for x in items if x["is_winner"])
        win_rate = wins / len(items)
        pnls = [x["pnl_pct"] for x in items if x["pnl_pct"] is not None]
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        existing = conn.execute(
            """SELECT stat_id FROM setup_performance_stats
               WHERE setup_class = ? AND direction = ?
               AND symbol IS ? AND regime IS ? AND moneyness IS ? AND dte_bucket IS ?""",
            (setup_class, direction, symbol, regime, moneyness, dte_bucket),
        ).fetchone()
        stat_id = existing["stat_id"] if existing else str(uuid.uuid4())
        conn.execute(
            """
            INSERT OR REPLACE INTO setup_performance_stats (
                stat_id, updated_at, symbol, setup_class, direction, regime,
                moneyness, dte_bucket, sample_count, win_rate, avg_pnl_pct, expectancy_modifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stat_id,
                now,
                symbol,
                setup_class,
                direction,
                regime,
                moneyness,
                dte_bucket,
                len(items),
                win_rate,
                avg_pnl,
                _modifier_from_pnl(avg_pnl),
            ),
        )
        updated += 1
    conn.commit()
    return updated
