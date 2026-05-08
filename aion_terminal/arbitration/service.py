from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aion_terminal.app.config import settings
from aion_terminal.arbitration.adaptive import (
    rebuild_expectancy_summaries,
    rebuild_setup_performance_stats,
)
from aion_terminal.arbitration.arbiter import run_arbitration
from aion_terminal.arbitration.schemas import ArbResult
from aion_terminal.utils.time_utils import utc_now_iso

BRIEF_DIR = Path("aion_terminal/data/briefs")


def _load_ranking(conn: sqlite3.Connection, symbol: str) -> dict | None:
    feat_row = conn.execute(
        "SELECT * FROM feature_snapshots WHERE symbol = ? ORDER BY snapshot_ts DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if not feat_row:
        return None
    spot = feat_row["spot"] or 0.0
    call_wall = feat_row["call_wall"]
    put_wall = feat_row["put_wall"]
    call_wall_pct = 0.0
    put_wall_pct = 0.0
    if spot and call_wall is not None:
        call_wall_pct = ((float(call_wall) - float(spot)) / float(spot)) * 100.0
    if spot and put_wall is not None:
        put_wall_pct = ((float(put_wall) - float(spot)) / float(spot)) * 100.0
    return {
        "symbol": symbol,
        "spot": spot,
        "regime": feat_row["regime"],
        "king_node": feat_row["king_node"],
        "call_wall": call_wall,
        "put_wall": put_wall,
        "call_wall_pct": call_wall_pct,
        "put_wall_pct": put_wall_pct,
    }


def _load_setup_candidates(conn: sqlite3.Connection, symbol: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM setup_candidates WHERE symbol = ? ORDER BY as_of_ts DESC LIMIT 3",
        (symbol,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_features(conn: sqlite3.Connection, symbol: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM feature_snapshots WHERE symbol = ? ORDER BY snapshot_ts DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    if out.get("features_json"):
        try:
            inner = json.loads(out["features_json"])
            if isinstance(inner, dict):
                out.update(inner)
        except Exception:
            pass
    return out


def _load_contracts(conn: sqlite3.Connection, symbol: str) -> dict | None:
    row = conn.execute(
        """
        SELECT option_symbol, expiry, strike, side, bid, ask, mark, delta, gamma,
               open_interest, volume, dte, underlying_price
        FROM raw_chain_snapshots
        WHERE symbol = ?
        ORDER BY snapshot_ts DESC, volume DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    bid = float(d.get("bid") or 0.0)
    ask = float(d.get("ask") or 0.0)
    mid = (bid + ask) / 2.0 if (bid and ask) else 0.0
    spread_pct = ((ask - bid) / mid * 100.0) if mid else 0.0
    best = {
        "contract_symbol": d.get("option_symbol"),
        "expiry": d.get("expiry"),
        "strike": d.get("strike"),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_pct": spread_pct,
        "delta": d.get("delta"),
        "gamma": d.get("gamma"),
        "open_interest": d.get("open_interest"),
        "volume": d.get("volume"),
        "dte": d.get("dte"),
        "liquidity_score": min(100.0, float(d.get("open_interest") or 0) / 10.0),
        "total_score": 50.0,
    }
    return {"best": best, "safer": None, "convex": None}


def _load_macro_brief() -> dict | None:
    if not BRIEF_DIR.exists():
        return None
    files = sorted(BRIEF_DIR.glob("*_morning_brief.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_memory_summary(conn: sqlite3.Connection, symbol: str) -> dict | None:
    for scope in (f"symbol:{symbol.upper()}", "global"):
        row = conn.execute(
            "SELECT * FROM agent_memory_summaries WHERE scope = ? ORDER BY updated_at DESC LIMIT 1",
            (scope,),
        ).fetchone()
        if row:
            return dict(row)
    return None


def _load_narrative_tags(conn: sqlite3.Connection, symbol: str) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT * FROM manual_narrative_tags WHERE symbol = ? ORDER BY tag_date DESC LIMIT 50",
            (symbol,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _load_rs_candidate(rs_conn: sqlite3.Connection | None, symbol: str) -> dict | None:
    if rs_conn is None:
        return None
    try:
        row = rs_conn.execute(
            "SELECT * FROM rs_candidates WHERE symbol = ? ORDER BY rowid DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def get_arbitration(symbol: str, conn: sqlite3.Connection, rs_conn: sqlite3.Connection | None = None) -> ArbResult:
    sym = symbol.upper()
    ranking = _load_ranking(conn, sym)
    setup_candidates = _load_setup_candidates(conn, sym)
    features = _load_features(conn, sym)
    contracts = _load_contracts(conn, sym)
    macro_brief = _load_macro_brief()
    memory_summary = _load_memory_summary(conn, sym)
    rs_candidate = _load_rs_candidate(rs_conn, sym)
    narrative_tags = _load_narrative_tags(conn, sym)

    return run_arbitration(
        symbol=sym,
        ranking=ranking,
        setup_candidates=setup_candidates,
        features=features,
        contracts=contracts,
        macro_brief=macro_brief,
        memory_summary=memory_summary,
        expectancy_data=None,
        rs_candidate=rs_candidate,
        narrative_tags=narrative_tags,
        conn=conn,
    )


def _arb_to_jsonable(arb: ArbResult) -> dict:
    d = asdict(arb)
    return d


def get_arbitration_candidates(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    watchlist = list(getattr(settings, "watchlist", []) or [])
    results: list[dict] = []
    for sym in watchlist:
        try:
            arb = get_arbitration(sym, conn)
            avg = arb.agreement_matrix.average()
            composite = arb.confidence * avg * arb.sizing_modifier
            entry = _arb_to_jsonable(arb)
            entry["composite_score"] = composite
            results.append(entry)
        except Exception:
            continue
    results.sort(key=lambda r: r.get("composite_score", 0.0), reverse=True)
    return results[: max(1, int(limit))]


def rebuild_adaptive_layer(conn: sqlite3.Connection) -> dict:
    scopes = rebuild_expectancy_summaries(conn)
    stats = rebuild_setup_performance_stats(conn)
    return {
        "scopes_updated": scopes,
        "stats_updated": stats,
        "timestamp": utc_now_iso(),
    }
