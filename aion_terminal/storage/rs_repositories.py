from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from aion_terminal.data.rs_engine import RSFeatures
from aion_terminal.utils.time_utils import utc_now_iso

RS_DB_PATH = "aion_terminal/storage/rs_universe.db"

RS_SCHEMA = """
CREATE TABLE IF NOT EXISTS rs_candidates (
    ticker TEXT PRIMARY KEY,
    rs_percentile REAL NOT NULL DEFAULT 0,
    rs_score REAL NOT NULL DEFAULT 0,
    rs_new_high_63d INTEGER NOT NULL DEFAULT 0,
    rs_new_high_before_price INTEGER NOT NULL DEFAULT 0,
    price_above_200d_ma INTEGER NOT NULL DEFAULT 0,
    price_above_50w_ma INTEGER NOT NULL DEFAULT 0,
    return_1y_pct REAL NOT NULL DEFAULT 0,
    close REAL NOT NULL DEFAULT 0,
    conditions_met INTEGER NOT NULL DEFAULT 0,
    passes_screen INTEGER NOT NULL DEFAULT 0,
    sector TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_passed TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    promoted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rs_scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    tickers_scanned INTEGER NOT NULL DEFAULT 0,
    tickers_passing INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_rs_candidates_percentile
ON rs_candidates(rs_percentile DESC);

CREATE INDEX IF NOT EXISTS idx_rs_candidates_passes
ON rs_candidates(passes_screen, expires_at);

CREATE INDEX IF NOT EXISTS idx_rs_scan_runs_at
ON rs_scan_runs(run_at DESC);
"""


def get_rs_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(RS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def bootstrap_rs_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(RS_SCHEMA)
    conn.commit()


def _expires_iso(days: int = 21) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def upsert_rs_candidate(
    conn: sqlite3.Connection,
    features: RSFeatures,
    sector: str = "",
) -> None:
    now = utc_now_iso()
    expires_at = _expires_iso(21)
    existing = conn.execute(
        "SELECT ticker, first_seen, promoted, created_at FROM rs_candidates WHERE ticker = ?",
        (features.ticker,),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE rs_candidates
            SET rs_percentile = ?,
                rs_score = ?,
                rs_new_high_63d = ?,
                rs_new_high_before_price = ?,
                price_above_200d_ma = ?,
                price_above_50w_ma = ?,
                return_1y_pct = ?,
                close = ?,
                conditions_met = ?,
                passes_screen = ?,
                sector = ?,
                last_passed = ?,
                expires_at = ?,
                updated_at = ?
            WHERE ticker = ?
            """,
            (
                features.rs_percentile,
                features.rs_score,
                int(features.rs_new_high_63d),
                int(features.rs_new_high_before_price),
                int(features.price_above_200d_ma),
                int(features.price_above_50w_ma),
                features.return_1y_pct,
                features.close,
                features.conditions_met,
                int(features.passes_screen),
                sector,
                now,
                expires_at,
                now,
                features.ticker,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO rs_candidates (
                ticker, rs_percentile, rs_score, rs_new_high_63d, rs_new_high_before_price,
                price_above_200d_ma, price_above_50w_ma, return_1y_pct, close, conditions_met,
                passes_screen, sector, first_seen, last_passed, expires_at, promoted, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                features.ticker,
                features.rs_percentile,
                features.rs_score,
                int(features.rs_new_high_63d),
                int(features.rs_new_high_before_price),
                int(features.price_above_200d_ma),
                int(features.price_above_50w_ma),
                features.return_1y_pct,
                features.close,
                features.conditions_met,
                int(features.passes_screen),
                sector,
                now,
                now,
                expires_at,
                0,
                now,
                now,
            ),
        )

    conn.commit()


def expire_stale_candidates(conn: sqlite3.Connection) -> int:
    now = utc_now_iso()
    cur = conn.execute(
        "DELETE FROM rs_candidates WHERE expires_at < ? AND promoted = 0",
        (now,),
    )
    conn.commit()
    return int(cur.rowcount or 0)


def query_passing_candidates(
    conn: sqlite3.Connection,
    min_percentile: float = 0.0,
    min_conditions: int = 3,
    include_expired: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    now = utc_now_iso()
    sql = """
        SELECT *
        FROM rs_candidates
        WHERE passes_screen = 1
          AND rs_percentile >= ?
          AND conditions_met >= ?
    """
    params: list[Any] = [min_percentile, min_conditions]
    if not include_expired:
        sql += " AND expires_at >= ?"
        params.append(now)

    sql += " ORDER BY rs_percentile DESC LIMIT ?"
    params.append(max(1, int(limit)))

    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def query_candidate_by_ticker(conn: sqlite3.Connection, ticker: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM rs_candidates WHERE ticker = ?", (ticker.upper(),)).fetchone()
    return dict(row) if row else None


def mark_promoted(conn: sqlite3.Connection, ticker: str, promoted: bool = True) -> None:
    conn.execute(
        "UPDATE rs_candidates SET promoted = ?, updated_at = ? WHERE ticker = ?",
        (int(promoted), utc_now_iso(), ticker.upper()),
    )
    conn.commit()


def log_scan_run(
    conn: sqlite3.Connection,
    tickers_scanned: int,
    tickers_passing: int,
    duration_seconds: float,
    errors: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO rs_scan_runs (run_at, tickers_scanned, tickers_passing, duration_seconds, errors)
        VALUES (?, ?, ?, ?, ?)
        """,
        (utc_now_iso(), tickers_scanned, tickers_passing, duration_seconds, errors),
    )
    conn.commit()


def query_recent_scan_runs(conn: sqlite3.Connection, limit: int = 5) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM rs_scan_runs ORDER BY run_at DESC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]
