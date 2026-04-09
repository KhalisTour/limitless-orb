from __future__ import annotations

import sqlite3
from typing import Any

from aion_terminal.models.dto import (
    FeatureSnapshotRecord,
    ManualNarrativeTagRecord,
    RawChainSnapshotRecord,
    SetupCandidateRecord,
    SetupOutcomeRecord,
    UnderlyingBarRecord,
)
from aion_terminal.utils.math_utils import as_float, as_int
from aion_terminal.utils.time_utils import utc_now_iso

# Legacy SQL used by current API behavior.
from aion_terminal.utils.math_utils import as_float, as_int

INSERT_RAW_CHAIN = """
INSERT OR IGNORE INTO raw_chain (
    timestamp, symbol, option_symbol, strike, expiry,
    type, gamma, open_interest, iv, dte, underlying_price
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

INSERT_COMPUTED_LEVELS = """
INSERT INTO computed_levels (
    timestamp, symbol, spot, king_node, call_wall, put_wall, regime
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

SELECT_LATEST_CHAIN = """
SELECT r.id, r.timestamp, r.symbol, r.option_symbol,
       r.strike, r.expiry, r.type, r.gamma,
       r.open_interest, r.iv, r.dte, r.underlying_price
FROM raw_chain r
INNER JOIN (
    SELECT expiry, MAX(timestamp) AS max_ts
    FROM raw_chain
    WHERE symbol = ?
    GROUP BY expiry
) latest ON r.expiry = latest.expiry
         AND r.timestamp = latest.max_ts
         AND r.symbol = ?
ORDER BY r.expiry, r.strike, r.option_symbol
"""

SELECT_PREVIOUS_LEVELS = """
SELECT timestamp, symbol, spot, king_node, call_wall, put_wall, regime
FROM computed_levels
WHERE symbol = ?
ORDER BY timestamp DESC
LIMIT 2
"""

# Research-grade SQL.
UPSERT_UNDERLYING_BAR = """
INSERT INTO underlying_bars (
    symbol, timeframe, bar_ts, open, high, low, close, volume, vwap, source, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, timeframe, bar_ts)
DO UPDATE SET
    open=excluded.open,
    high=excluded.high,
    low=excluded.low,
    close=excluded.close,
    volume=excluded.volume,
    vwap=excluded.vwap,
    source=excluded.source,
    updated_at=excluded.updated_at
"""

INSERT_CHAIN_SNAPSHOT = """
INSERT OR IGNORE INTO raw_chain_snapshots (
    snapshot_ts, symbol, expiry, option_symbol, side, strike, bid, ask, last, mark,
    iv, delta, gamma, theta, vega, rho, open_interest, volume, dte, underlying_price,
    source, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

INSERT_FEATURE_SNAPSHOT = """
INSERT INTO feature_snapshots (
    snapshot_ts, symbol, expiry, dte, spot, regime, king_node, call_wall, put_wall,
    flip_zone, features_json, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

INSERT_SETUP_CANDIDATE = """
INSERT INTO setup_candidates (
    candidate_id, as_of_ts, symbol, setup_class, direction, timeframe, expiry, option_type,
    strike, score, rank, confidence, rationale_json, status, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(candidate_id)
DO UPDATE SET
    as_of_ts=excluded.as_of_ts,
    symbol=excluded.symbol,
    setup_class=excluded.setup_class,
    direction=excluded.direction,
    timeframe=excluded.timeframe,
    expiry=excluded.expiry,
    option_type=excluded.option_type,
    strike=excluded.strike,
    score=excluded.score,
    rank=excluded.rank,
    confidence=excluded.confidence,
    rationale_json=excluded.rationale_json,
    status=excluded.status,
    updated_at=excluded.updated_at
"""

INSERT_SETUP_OUTCOME = """
INSERT INTO setup_outcomes (
    candidate_id, outcome_ts, pnl_abs, pnl_pct, max_favorable_excursion,
    max_adverse_excursion, hold_minutes, is_winner, outcome_label, notes,
    created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

UPSERT_MANUAL_TAG = """
INSERT INTO manual_narrative_tags (
    symbol, tag_date, tag_key, tag_value, context_json, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, tag_date, tag_key)
DO UPDATE SET
    tag_value=excluded.tag_value,
    context_json=excluded.context_json,
    updated_at=excluded.updated_at
"""

SELECT_LATEST_FEATURE_SNAPSHOT_BY_SYMBOL = """
SELECT snapshot_ts, symbol, expiry, dte, spot, regime, king_node, call_wall, put_wall,
       flip_zone, features_json, created_at, updated_at
FROM feature_snapshots
WHERE symbol = ?
ORDER BY snapshot_ts DESC
LIMIT 1
"""

SELECT_RANKED_CANDIDATES_BY_RANGE = """
SELECT candidate_id, as_of_ts, symbol, setup_class, direction, timeframe, expiry, option_type,
       strike, score, rank, confidence, rationale_json, status, created_at, updated_at
FROM setup_candidates
WHERE as_of_ts BETWEEN ? AND ?
  AND (? IS NULL OR symbol = ?)
  AND (? IS NULL OR setup_class = ?)
ORDER BY as_of_ts DESC, score DESC
LIMIT ?
"""


def _resolve_audit_timestamps(created_at: str, updated_at: str) -> tuple[str, str]:
    now = utc_now_iso()
    return (created_at or now, updated_at or now)


# -------- Legacy behavior functions --------

def save_contracts(conn: sqlite3.Connection, contracts: list[dict[str, Any]]) -> int:
    """Insert normalized contracts and return rows inserted."""
    if not contracts:
        return 0

    before_changes = conn.total_changes
    conn.executemany(
        INSERT_RAW_CHAIN,
        [
            (
                c.get("timestamp"),
                c.get("symbol"),
                c.get("option_symbol"),
                as_float(c.get("strike")),
                c.get("expiry"),
                c.get("type"),
                as_float(c.get("gamma")),
                as_int(c.get("open_interest")),
                as_float(c.get("iv")),
                as_int(c.get("dte")),
                as_float(c.get("underlying_price")),
            )
            for c in contracts
        ],
    )
    conn.commit()
    return conn.total_changes - before_changes


def query_latest_chain(conn: sqlite3.Connection, symbol: str) -> list[dict[str, Any]]:
    rows = conn.execute(SELECT_LATEST_CHAIN, (symbol, symbol)).fetchall()
    return [dict(row) for row in rows]


def save_levels(conn: sqlite3.Connection, levels: dict[str, Any], timestamp: str) -> None:
    conn.execute(
        INSERT_COMPUTED_LEVELS,
        (
            timestamp,
            levels.get("symbol"),
            as_float(levels.get("spot")),
            as_float(levels.get("king_node")),
            as_float(levels.get("call_wall")) if levels.get("call_wall") is not None else None,
            as_float(levels.get("put_wall")) if levels.get("put_wall") is not None else None,
            levels.get("regime"),
        ),
    )
    conn.commit()


def query_previous_levels(conn: sqlite3.Connection, symbol: str) -> dict[str, Any] | None:
    rows = conn.execute(SELECT_PREVIOUS_LEVELS, (symbol,)).fetchall()
    if len(rows) < 2:
        return None
    return dict(rows[1])


def query_expiries(conn: sqlite3.Connection, symbol: str, dte_max: int = 60) -> list[str]:
    rows = query_latest_chain(conn, symbol)
    return sorted({r.get("expiry") for r in rows if r.get("expiry") and r.get("dte", 999) <= dte_max})


# -------- Research engine repository functions --------
def upsert_underlying_bars(conn: sqlite3.Connection, bars: list[UnderlyingBarRecord]) -> int:
    if not bars:
        return 0

    before_changes = conn.total_changes
    payload = []
    for bar in bars:
        created_at, updated_at = _resolve_audit_timestamps(bar.created_at, bar.updated_at)
        payload.append(
            (
                bar.symbol,
                bar.timeframe,
                bar.bar_ts,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.vwap,
                bar.source,
                created_at,
                updated_at,
            )
        )

    conn.executemany(UPSERT_UNDERLYING_BAR, payload)
    conn.commit()
    return conn.total_changes - before_changes


def insert_chain_snapshots(conn: sqlite3.Connection, snapshots: list[RawChainSnapshotRecord]) -> int:
    if not snapshots:
        return 0

    before_changes = conn.total_changes
    payload = []
    for snap in snapshots:
        created_at, updated_at = _resolve_audit_timestamps(snap.created_at, snap.updated_at)
        payload.append(
            (
                snap.snapshot_ts,
                snap.symbol,
                snap.expiry,
                snap.option_symbol,
                snap.side,
                snap.strike,
                snap.bid,
                snap.ask,
                snap.last,
                snap.mark,
                snap.iv,
                snap.delta,
                snap.gamma,
                snap.theta,
                snap.vega,
                snap.rho,
                snap.open_interest,
                snap.volume,
                snap.dte,
                snap.underlying_price,
                snap.source,
                created_at,
                updated_at,
            )
        )

    conn.executemany(INSERT_CHAIN_SNAPSHOT, payload)
    conn.commit()
    return conn.total_changes - before_changes


def insert_feature_snapshots(conn: sqlite3.Connection, features: list[FeatureSnapshotRecord]) -> int:
    if not features:
        return 0

    before_changes = conn.total_changes
    payload = []
    for feature in features:
        created_at, updated_at = _resolve_audit_timestamps(feature.created_at, feature.updated_at)
        payload.append(
            (
                feature.snapshot_ts,
                feature.symbol,
                feature.expiry,
                feature.dte,
                feature.spot,
                feature.regime,
                feature.king_node,
                feature.call_wall,
                feature.put_wall,
                feature.flip_zone,
                feature.features_json,
                created_at,
                updated_at,
            )
        )

    conn.executemany(INSERT_FEATURE_SNAPSHOT, payload)
    conn.commit()
    return conn.total_changes - before_changes


def insert_setup_candidates(conn: sqlite3.Connection, candidates: list[SetupCandidateRecord]) -> int:
    if not candidates:
        return 0

    before_changes = conn.total_changes
    payload = []
    for candidate in candidates:
        created_at, updated_at = _resolve_audit_timestamps(candidate.created_at, candidate.updated_at)
        payload.append(
            (
                candidate.candidate_id,
                candidate.as_of_ts,
                candidate.symbol,
                candidate.setup_class,
                candidate.direction,
                candidate.timeframe,
                candidate.expiry,
                candidate.option_type,
                candidate.strike,
                candidate.score,
                candidate.rank,
                candidate.confidence,
                candidate.rationale_json,
                candidate.status,
                created_at,
                updated_at,
            )
        )

    conn.executemany(INSERT_SETUP_CANDIDATE, payload)
    conn.commit()
    return conn.total_changes - before_changes


def insert_setup_outcomes(conn: sqlite3.Connection, outcomes: list[SetupOutcomeRecord]) -> int:
    if not outcomes:
        return 0

    before_changes = conn.total_changes
    payload = []
    for outcome in outcomes:
        created_at, updated_at = _resolve_audit_timestamps(outcome.created_at, outcome.updated_at)
        payload.append(
            (
                outcome.candidate_id,
                outcome.outcome_ts,
                outcome.pnl_abs,
                outcome.pnl_pct,
                outcome.max_favorable_excursion,
                outcome.max_adverse_excursion,
                outcome.hold_minutes,
                outcome.is_winner,
                outcome.outcome_label,
                outcome.notes,
                created_at,
                updated_at,
            )
        )

    conn.executemany(INSERT_SETUP_OUTCOME, payload)
    conn.commit()
    return conn.total_changes - before_changes


def upsert_manual_narrative_tags(conn: sqlite3.Connection, tags: list[ManualNarrativeTagRecord]) -> int:
    if not tags:
        return 0

    before_changes = conn.total_changes
    payload = []
    for tag in tags:
        created_at, updated_at = _resolve_audit_timestamps(tag.created_at, tag.updated_at)
        payload.append((tag.symbol, tag.tag_date, tag.tag_key, tag.tag_value, tag.context_json, created_at, updated_at))

    conn.executemany(UPSERT_MANUAL_TAG, payload)
    conn.commit()
    return conn.total_changes - before_changes


def query_latest_feature_snapshot_by_symbol(conn: sqlite3.Connection, symbol: str) -> FeatureSnapshotRecord | None:
    row = conn.execute(SELECT_LATEST_FEATURE_SNAPSHOT_BY_SYMBOL, (symbol,)).fetchone()
    if not row:
        return None
    return FeatureSnapshotRecord(**dict(row))


def query_ranked_candidates_by_date_range(
    conn: sqlite3.Connection,
    start_ts: str,
    end_ts: str,
    symbol: str | None = None,
    setup_class: str | None = None,
    limit: int = 100,
) -> list[SetupCandidateRecord]:
    rows = conn.execute(
        SELECT_RANKED_CANDIDATES_BY_RANGE,
        (start_ts, end_ts, symbol, symbol, setup_class, setup_class, limit),
    ).fetchall()
    return [SetupCandidateRecord(**dict(row)) for row in rows]
