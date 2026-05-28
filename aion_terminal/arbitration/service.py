from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aion_terminal.arbitration.adaptive import (
    rebuild_expectancy_summaries,
    rebuild_setup_performance_stats,
)
from aion_terminal.arbitration.arbiter import run_arbitration
from aion_terminal.arbitration.schemas import ArbResult
from aion_terminal.utils.time_utils import utc_now_iso


BRIEF_DIR = Path("aion_terminal/data/briefs")


def _safe_dict(blob: Any) -> dict | None:
    if blob is None:
        return None
    if isinstance(blob, dict):
        return blob
    if isinstance(blob, str):
        try:
            out = json.loads(blob)
            return out if isinstance(out, dict) else None
        except Exception:
            return None
    return None


def _load_ranking(conn: sqlite3.Connection, symbol: str) -> dict | None:
    feat_row = conn.execute(
        "SELECT * FROM feature_snapshots WHERE symbol = ? ORDER BY snapshot_ts DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    level_row = conn.execute(
        "SELECT * FROM computed_levels WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if not feat_row and not level_row:
        return None
    feat = dict(feat_row) if feat_row else {}
    lvl = dict(level_row) if level_row else {}
    spot = feat.get("spot") or lvl.get("spot")
    regime = feat.get("regime") or lvl.get("regime")
    call_wall = feat.get("call_wall") or lvl.get("call_wall")
    put_wall = feat.get("put_wall") or lvl.get("put_wall")
    king_node = feat.get("king_node") or lvl.get("king_node")
    return {
        "symbol": symbol,
        "spot": spot,
        "regime": regime,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "king_node": king_node,
        "dealer_structure": {
            "call_wall": call_wall,
            "put_wall": put_wall,
            "king_node": king_node,
        },
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
    return dict(row)


def _load_contracts(conn: sqlite3.Connection, symbol: str) -> dict | None:
    # AUDIT FIX: field name corrected — selecting actual raw_chain_snapshots
    # columns (side, strike, bid, ask, iv, delta, gamma, theta, open_interest,
    # volume, dte, expiry/underlying_price), not the SELECT_LATEST_CHAIN alias.
    rows = conn.execute(
        """
        SELECT option_symbol, side, strike, expiry, bid, ask, mark, iv, delta, gamma,
               theta, vega, open_interest, volume, dte, underlying_price
        FROM raw_chain_snapshots
        WHERE symbol = ?
          AND snapshot_ts = (SELECT MAX(snapshot_ts) FROM raw_chain_snapshots WHERE symbol = ?)
        ORDER BY volume DESC NULLS LAST, open_interest DESC NULLS LAST
        LIMIT 20
        """.replace("NULLS LAST", ""),
        (symbol, symbol),
    ).fetchall()
    if not rows:
        return None
    scored = []
    for r in rows:
        d = dict(r)
        bid = d.get("bid") or 0
        ask = d.get("ask") or 0
        mid = (bid + ask) / 2.0 if (bid and ask) else None
        spread_pct = ((ask - bid) / mid) if (mid and mid > 0 and ask > bid) else None
        vol = d.get("volume") or 0
        oi = d.get("open_interest") or 0
        liq = min(1.0, (vol / 1000.0) * 0.5 + (oi / 5000.0) * 0.5) if (vol or oi) else 0.0
        total = 50.0
        if spread_pct is not None:
            total -= min(30.0, spread_pct * 100.0)
        total += liq * 30.0
        d["spread_pct"] = spread_pct
        d["liquidity_score"] = liq
        d["total_score"] = total
        d["contract_symbol"] = d.get("option_symbol")
        scored.append(d)
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    return {"best": scored[0], "all_scored": scored[:10]}


def _load_macro_brief() -> dict | None:
    if not BRIEF_DIR.exists():
        return None
    files = sorted(BRIEF_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        raw = json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    nested = raw.get("json_plan") if isinstance(raw.get("json_plan"), dict) else {}
    return {
        "regime": raw.get("regime") or nested.get("regime"),
        "risk_level": raw.get("risk_level") or nested.get("risk_level"),
        "dominant_signal": raw.get("dominant_signal") or nested.get("dominant_signal"),
        "sector_leaders": raw.get("sector_leaders") or nested.get("sector_leaders") or [],
        "sector_laggards": raw.get("sector_laggards") or nested.get("sector_laggards") or [],
        "narrative_tags": raw.get("narrative_tags") or nested.get("narrative_tags") or [],
    }


def _load_memory_summary(conn: sqlite3.Connection, symbol: str) -> dict | None:
    for scope in (f"symbol:{symbol}", "global"):
        row = conn.execute(
            "SELECT * FROM agent_memory_summaries WHERE scope = ? ORDER BY updated_at DESC LIMIT 1",
            (scope,),
        ).fetchone()
        if row:
            return dict(row)
    return None


def _load_expectancy(conn: sqlite3.Connection, setup_class: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM adaptive_expectancy WHERE scope = ? ORDER BY updated_at DESC LIMIT 1",
        (f"setup:{setup_class}",),
    ).fetchone()
    return dict(row) if row else None


def _load_rs_candidate(rs_conn: sqlite3.Connection | None, symbol: str) -> dict | None:
    if rs_conn is None:
        return None
    try:
        row = rs_conn.execute(
            "SELECT * FROM rs_candidates WHERE symbol = ? ORDER BY rowid DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None


def _load_narrative_tags(conn: sqlite3.Connection, symbol: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM manual_narrative_tags
        WHERE symbol = ?
        ORDER BY tag_date DESC LIMIT 20
        """,
        (symbol,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_arbitration(symbol: str, conn: sqlite3.Connection, rs_conn: sqlite3.Connection | None = None) -> ArbResult:
    symbol = symbol.upper()
    ranking = _load_ranking(conn, symbol)
    setup_candidates = _load_setup_candidates(conn, symbol)
    features = _load_features(conn, symbol)
    contracts = _load_contracts(conn, symbol)
    macro_brief = _load_macro_brief()
    memory = _load_memory_summary(conn, symbol)
    setup_class = setup_candidates[0]["setup_class"] if setup_candidates else "technical_dealer_watch"
    expectancy = _load_expectancy(conn, setup_class)
    rs_candidate = _load_rs_candidate(rs_conn, symbol)
    narrative_tags = _load_narrative_tags(conn, symbol)

    return run_arbitration(
        symbol=symbol,
        ranking=ranking,
        setup_candidates=setup_candidates,
        features=features,
        contracts=contracts,
        macro_brief=macro_brief,
        memory_summary=memory,
        expectancy_data=expectancy,
        rs_candidate=rs_candidate,
        narrative_tags=narrative_tags,
        conn=conn,
    )


def get_arbitration_candidates(conn: sqlite3.Connection, limit: int = 20, watchlist: list[str] | None = None) -> list[dict]:
    if watchlist is None:
        from aion_terminal.app.config import settings
        watchlist = settings.watchlist
    results: list[tuple[float, dict]] = []
    for sym in watchlist:
        try:
            arb = get_arbitration(sym, conn)
        except Exception:
            continue
        score = (arb.confidence or 0.0) * arb.agreement_matrix.average() * (arb.sizing_modifier or 0.0)
        results.append((score, _arb_to_json(arb)))
    results.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in results[:limit]]


def _arb_to_json(arb: ArbResult) -> dict:
    """Convert ArbResult to JSON-safe dict with all fields present."""
    out = asdict(arb)
    # Ensure optional nested keys not None-omitted
    for opt_key in ("required_trigger", "kill_switch"):
        if out.get(opt_key) is None:
            out[opt_key] = None
    return out


def rebuild_adaptive_layer(conn: sqlite3.Connection) -> dict:
    scopes_updated = rebuild_expectancy_summaries(conn)
    stats_updated = rebuild_setup_performance_stats(conn)
    return {
        "scopes_updated": scopes_updated,
        "stats_updated": stats_updated,
        "timestamp": utc_now_iso(),
    }
