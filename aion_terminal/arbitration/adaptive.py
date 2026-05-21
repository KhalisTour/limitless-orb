from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from aion_terminal.arbitration.schemas import AgreementMatrix
from aion_terminal.utils.time_utils import utc_now_iso


def _scope_key(setup_class: str, regime: str, direction: str, dte_bucket: str, moneyness: str) -> str:
    return f"setup:{setup_class}|regime:{regime}|dir:{direction}|dte:{dte_bucket}|m:{moneyness}"


def _query_expectancy_scope(conn: sqlite3.Connection, scope: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM adaptive_expectancy WHERE scope = ? ORDER BY updated_at DESC LIMIT 1",
        (scope,),
    ).fetchone()
    return dict(row) if row else None


def compute_expectancy_modifier(
    conn: sqlite3.Connection,
    setup_class: str,
    regime: str,
    direction: str,
    dte_bucket: str,
    moneyness: str,
) -> float:
    candidates = [
        _scope_key(setup_class, regime, direction, dte_bucket, moneyness),
        f"setup:{setup_class}|regime:{regime}|dir:{direction}",
        f"setup:{setup_class}|dir:{direction}",
        f"setup:{setup_class}",
    ]
    for scope in candidates:
        row = _query_expectancy_scope(conn, scope)
        if row and row.get("expectancy_modifier") is not None:
            try:
                return max(-1.0, min(1.0, float(row["expectancy_modifier"])))
            except (TypeError, ValueError):
                continue
    return 0.0


def compute_memory_penalty(conn: sqlite3.Connection, symbol: str, setup_class: str) -> dict:
    scopes = ["global", f"symbol:{symbol.upper()}", f"setup:{setup_class}"]
    total_penalty = 0.0
    reasons: list[str] = []
    for scope in scopes:
        row = conn.execute(
            "SELECT summary_json FROM agent_memory_summaries WHERE scope = ? ORDER BY updated_at DESC LIMIT 1",
            (scope,),
        ).fetchone()
        if not row:
            continue
        try:
            payload = json.loads(row["summary_json"] or "{}")
        except Exception:
            continue
        warnings = payload.get("common_warnings") or []
        behaviors = payload.get("behavioral_notes") or []
        text = " ".join(str(w).lower() for w in [*warnings, *behaviors])
        if "early_exit" in text or "early exits" in text:
            total_penalty += 0.10
            reasons.append("memory_penalty_early_exit")
        if "far_otm" in text or "far otm" in text:
            total_penalty += 0.15
            reasons.append("memory_penalty_far_otm_pre_confirmation")
        if "oversized" in text or "oversize" in text:
            total_penalty += 0.10
            reasons.append("memory_penalty_oversized_convex")
        if "plan_deviation_losses" in text:
            total_penalty += 0.08
            reasons.append("memory_penalty_plan_deviation")

    return {
        "total_penalty": min(0.5, total_penalty),
        "reasons": list(dict.fromkeys(reasons)),
    }


def determine_contract_role(
    agreement: AgreementMatrix,
    conflicts: list[str],
    expectancy_modifier: float,
    memory_penalty: float,
) -> str:
    avg = agreement.average()
    critical = {"no_liquid_contracts", "macro_risk_not_supportive"}
    has_critical = any(c in critical for c in conflicts)

    if avg < 0.35 or "no_liquid_contracts" in conflicts:
        return "no_trade"
    if memory_penalty > 0.3 or has_critical:
        return "safer_only"
    if avg >= 0.70 and expectancy_modifier >= 0.0 and not conflicts:
        return "convex"
    if avg < 0.50:
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
    base *= max(0.0, min(1.0, confidence))
    base *= max(0.0, min(1.0, agreement_avg))
    base *= max(0.0, 1.0 - memory_penalty)
    if not has_acceptance:
        base *= 0.5
    if str(regime or "").lower() in {"range", "rangebound", "compression"}:
        base *= 0.7
    return max(0.1, min(1.0, base))


def _dte_bucket(dte: int | float | None) -> str:
    if dte is None:
        return "unknown"
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


def rebuild_expectancy_summaries(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT sc.setup_class AS setup_class,
               sc.direction AS direction,
               sc.symbol AS symbol,
               so.pnl_pct AS pnl_pct,
               so.max_favorable_excursion AS mfe,
               so.max_adverse_excursion AS mae,
               so.hold_minutes AS hold_minutes,
               so.is_winner AS is_winner
        FROM setup_outcomes so
        JOIN setup_candidates sc ON sc.candidate_id = so.candidate_id
        """
    ).fetchall()

    buckets: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        scopes = [
            f"setup:{d.get('setup_class') or 'unknown'}",
            f"setup:{d.get('setup_class') or 'unknown'}|dir:{d.get('direction') or 'unknown'}",
        ]
        for s in scopes:
            buckets.setdefault(s, []).append(d)

    updated = 0
    now = utc_now_iso()
    for scope, items in buckets.items():
        if len(items) < 5:
            continue
        n = len(items)
        wins = sum(1 for i in items if (i.get("is_winner") or 0))
        win_rate = wins / n if n else 0.0
        pnls = [float(i["pnl_pct"]) for i in items if i.get("pnl_pct") is not None]
        mfes = [float(i["mfe"]) for i in items if i.get("mfe") is not None]
        maes = [float(i["mae"]) for i in items if i.get("mae") is not None]
        holds = [float(i["hold_minutes"]) for i in items if i.get("hold_minutes") is not None]

        avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        avg_mfe = sum(mfes) / len(mfes) if mfes else 0.0
        avg_mae = sum(maes) / len(maes) if maes else 0.0
        avg_hold = sum(holds) / len(holds) if holds else 0.0

        modifier = max(-1.0, min(1.0, avg_pnl / 20.0))

        raw_stats = {
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl,
            "avg_mfe_pct": avg_mfe,
            "avg_mae_pct": avg_mae,
            "avg_hold_minutes": avg_hold,
            "sample_count": n,
        }

        existing = conn.execute(
            "SELECT exp_id FROM adaptive_expectancy WHERE scope = ?", (scope,)
        ).fetchone()
        exp_id = existing["exp_id"] if existing else str(uuid.uuid4())

        conn.execute(
            """
            INSERT INTO adaptive_expectancy (
                exp_id, updated_at, scope, sample_count, win_rate,
                avg_pnl_pct, avg_mfe_pct, avg_mae_pct, avg_hold_minutes,
                expectancy_modifier, raw_stats_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                updated_at=excluded.updated_at,
                sample_count=excluded.sample_count,
                win_rate=excluded.win_rate,
                avg_pnl_pct=excluded.avg_pnl_pct,
                avg_mfe_pct=excluded.avg_mfe_pct,
                avg_mae_pct=excluded.avg_mae_pct,
                avg_hold_minutes=excluded.avg_hold_minutes,
                expectancy_modifier=excluded.expectancy_modifier,
                raw_stats_json=excluded.raw_stats_json
            """,
            (
                exp_id, now, scope, n, win_rate,
                avg_pnl, avg_mfe, avg_mae, avg_hold,
                modifier, json.dumps(raw_stats),
            ),
        )
        updated += 1

    conn.commit()
    return updated


def rebuild_setup_performance_stats(conn: sqlite3.Connection) -> int:
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

    buckets: dict[tuple, list[dict]] = {}
    for r in rows:
        d = dict(r)
        key = (None, d.get("setup_class") or "unknown", d.get("direction") or "unknown", None, None, None)
        buckets.setdefault(key, []).append(d)

    updated = 0
    now = utc_now_iso()
    for (symbol, setup_class, direction, regime, moneyness, dte_bucket), items in buckets.items():
        n = len(items)
        if n < 1:
            continue
        wins = sum(1 for i in items if (i.get("is_winner") or 0))
        win_rate = wins / n if n else 0.0
        pnls = [float(i["pnl_pct"]) for i in items if i.get("pnl_pct") is not None]
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        modifier = max(-1.0, min(1.0, avg_pnl / 20.0))

        existing = conn.execute(
            """SELECT stat_id FROM setup_performance_stats
               WHERE COALESCE(symbol,'') = COALESCE(?, '')
                 AND setup_class = ?
                 AND COALESCE(direction,'') = COALESCE(?, '')
                 AND COALESCE(regime,'') = COALESCE(?, '')
                 AND COALESCE(moneyness,'') = COALESCE(?, '')
                 AND COALESCE(dte_bucket,'') = COALESCE(?, '')""",
            (symbol, setup_class, direction, regime, moneyness, dte_bucket),
        ).fetchone()
        stat_id = existing["stat_id"] if existing else str(uuid.uuid4())

        conn.execute(
            """
            INSERT OR REPLACE INTO setup_performance_stats (
                stat_id, updated_at, symbol, setup_class, direction, regime,
                moneyness, dte_bucket, sample_count, win_rate, avg_pnl_pct,
                expectancy_modifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stat_id, now, symbol, setup_class, direction, regime,
                moneyness, dte_bucket, n, win_rate, avg_pnl, modifier,
            ),
        )
        updated += 1

    conn.commit()
    return updated
