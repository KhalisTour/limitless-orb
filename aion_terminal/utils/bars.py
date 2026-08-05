"""Shared loading of clean daily bar series.

``underlying_bars`` carries two corruptions that every consumer has to filter
out, and each one that rolled its own query got it wrong differently:

* Mixed timeframes. The table holds ``1H``, ``4H``, ``15M`` and ``30M`` rows
  alongside daily ones, so an unfiltered read computes "daily" indicators
  across intraday bars.
* Duplicate calendar days. The same trading day is written under more than one
  daily label (``D`` and ``1D``) by separate fetches, with slightly different
  closes and volumes. An unfiltered 60-row read returned as few as 35 distinct
  days — roughly 40% repeats — which smooths EMAs, deflates ATR through
  zero-range duplicate days, double-counts VWAP volume and depresses RVOL.

Both are handled here once so the technical channel, the ranking service and
the outcome scorer cannot drift apart.
"""

from __future__ import annotations

import sqlite3
from typing import Any

DAILY_TIMEFRAMES = ("D", "1D", "DAILY")
_TIMEFRAME_SQL = "UPPER(timeframe) IN ('D', '1D', 'DAILY')"

_COLUMNS = "symbol, timeframe, bar_ts, open, high, low, close, volume, vwap"


def _dedupe_by_day(rows: list[Any], limit: int, newest_first: bool) -> list[Any]:
    """Keep one row per calendar day, preferring the first seen."""
    seen: set[str] = set()
    kept = []
    for r in rows:
        day = str(r["bar_ts"])[:10]
        if day in seen:
            continue
        seen.add(day)
        kept.append(r)
        if len(kept) >= limit:
            break
    return kept if newest_first else kept


def load_recent_daily_bars(conn: sqlite3.Connection, symbol: str, limit: int = 60) -> list[dict]:
    """Most recent ``limit`` distinct daily bars, oldest first.

    Indicator functions assume chronological order, so the result is reversed
    after deduplication.
    """
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM underlying_bars WHERE symbol = ? AND {_TIMEFRAME_SQL} "
        "ORDER BY bar_ts DESC LIMIT ?",
        (symbol, limit * 4),
    ).fetchall()
    return [dict(r) for r in reversed(_dedupe_by_day(rows, limit, newest_first=True))]


def load_daily_bars_after(
    conn: sqlite3.Connection, symbol: str, as_of_ts: str, limit: int
) -> list[dict]:
    """Distinct daily bars at or after ``as_of_ts``, oldest first.

    Used to score a candidate forward. Deduplication matters more here than
    anywhere else: a duplicated day would be counted as two days of holding
    period, understating the horizon and inflating the apparent result.
    """
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM underlying_bars WHERE symbol = ? AND bar_ts >= ? AND {_TIMEFRAME_SQL} "
        "ORDER BY bar_ts ASC LIMIT ?",
        (symbol, as_of_ts, limit * 4),
    ).fetchall()
    return [dict(r) for r in _dedupe_by_day(rows, limit, newest_first=False)]
