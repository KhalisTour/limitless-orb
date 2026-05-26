"""Infrastructure / health routes: cache invalidation and data freshness."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from aion_terminal.app.config import settings
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.utils.time_utils import utc_now_iso

router = APIRouter(tags=["infrastructure"])
SCHEMA_PATH = "aion_terminal/storage/schema.sql"


_RANKING_CACHE: dict[str, Any] = {}


def _clear_ranking_cache() -> int:
    n = len(_RANKING_CACHE)
    _RANKING_CACHE.clear()
    return n


@router.post("/cache/invalidate")
async def invalidate_ranking_cache():
    """Clear the in-memory ranking cache so the next request rebuilds from DB."""
    cleared = _clear_ranking_cache()
    note = "cache_cleared" if cleared else "no_cache_entries"
    return {
        "status": "invalidated",
        "timestamp": utc_now_iso(),
        "entries_cleared": cleared,
        "note": note,
    }


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _hours_ago(ts: str | None, now: datetime) -> float | None:
    dt = _parse_iso(ts)
    if dt is None:
        return None
    delta = now - dt
    return round(delta.total_seconds() / 3600.0, 2)


def _status_for_hours(hours: float | None) -> str:
    if hours is None:
        return "missing"
    if hours < 6.0:
        return "fresh"
    if hours <= 24.0:
        return "stale"
    return "missing"


def _worst(*statuses: str) -> str:
    order = {"fresh": 0, "stale": 1, "missing": 2}
    return max(statuses, key=lambda s: order.get(s, 2))


@router.get("/health/data-freshness")
async def get_data_freshness():
    """Return per-symbol data age + morning brief freshness."""
    now = datetime.now(timezone.utc)
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        chain_rows = conn.execute(
            "SELECT symbol, MAX(snapshot_ts) AS last_ts FROM raw_chain_snapshots GROUP BY symbol"
        ).fetchall()
        bars_rows = conn.execute(
            """
            SELECT symbol, MAX(bar_ts) AS last_ts FROM underlying_bars
            WHERE timeframe IN ('1D','D','daily','1d')
            GROUP BY symbol
            """
        ).fetchall()
        feat_rows = conn.execute(
            "SELECT symbol, MAX(snapshot_ts) AS last_ts FROM feature_snapshots GROUP BY symbol"
        ).fetchall()
        brief_row = conn.execute(
            "SELECT MAX(generated_at) AS last_ts FROM morning_briefs"
        ).fetchone()
    finally:
        conn.close()

    chain_map = {r["symbol"]: r["last_ts"] for r in chain_rows}
    bars_map = {r["symbol"]: r["last_ts"] for r in bars_rows}
    feat_map = {r["symbol"]: r["last_ts"] for r in feat_rows}

    symbols = sorted(set(chain_map) | set(bars_map) | set(feat_map))
    out_symbols: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        chain_last = chain_map.get(sym)
        bars_last = bars_map.get(sym)
        feat_last = feat_map.get(sym)
        chain_h = _hours_ago(chain_last, now)
        bars_h = _hours_ago(bars_last, now)
        feat_h = _hours_ago(feat_last, now)
        overall = _worst(
            _status_for_hours(chain_h),
            _status_for_hours(bars_h),
            _status_for_hours(feat_h),
        )
        out_symbols[sym] = {
            "chain_last": chain_last,
            "chain_hours_ago": chain_h,
            "bars_last": bars_last,
            "bars_hours_ago": bars_h,
            "features_last": feat_last,
            "features_hours_ago": feat_h,
            "overall_status": overall,
        }

    brief_last = brief_row["last_ts"] if brief_row else None
    brief_h = _hours_ago(brief_last, now)
    return {
        "checked_at": utc_now_iso(),
        "morning_brief": {
            "last_run": brief_last,
            "hours_ago": brief_h,
            "status": _status_for_hours(brief_h),
        },
        "symbols": out_symbols,
    }
