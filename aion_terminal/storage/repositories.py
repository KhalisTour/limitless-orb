from __future__ import annotations

import sqlite3
from typing import Any

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
