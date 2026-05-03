"""Dashboard service: assemble unified frontend dashboard from DB/services."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aion_terminal.app.config import settings
from aion_terminal.services.ranking_service import get_rankings_unified
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)
SCHEMA_PATH = "aion_terminal/storage/schema.sql"


def load_latest_brief() -> dict[str, Any] | None:
    """Load the latest saved morning brief from disk."""
    briefs_dir = Path("aion_terminal/data/briefs")
    if not briefs_dir.exists():
        return None
    
    # Find most recent brief file
    brief_files = sorted(briefs_dir.glob("*_morning_brief.json"), reverse=True)
    if not brief_files:
        return None
    
    try:
        latest_brief = json.loads(brief_files[0].read_text(encoding="utf-8"))
        return latest_brief
    except Exception as exc:
        logger.warning("Failed to load latest brief: %s", exc)
        return None


def get_system_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather system status counts and timestamps."""
    try:
        # Last snapshot timestamp
        last_ts_row = conn.execute(
            "SELECT MAX(created_at) FROM raw_chain_snapshots"
        ).fetchone()
        last_snapshot_ts = last_ts_row[0] if last_ts_row and last_ts_row[0] else None
        
        # Counts
        raw_chain_snapshots = conn.execute(
            "SELECT COUNT(*) FROM raw_chain_snapshots"
        ).fetchone()[0]
        
        feature_snapshots = conn.execute(
            "SELECT COUNT(*) FROM feature_snapshots"
        ).fetchone()[0]
        
        setup_candidates = conn.execute(
            "SELECT COUNT(*) FROM setup_candidates"
        ).fetchone()[0]
        
        stale_minutes = _stale_minutes(last_snapshot_ts)
        return {
            "cache_only": settings.cache_only,
            "marketdata_enabled": settings.marketdata_enabled and not settings.cache_only,
            "last_snapshot_ts": last_snapshot_ts,
            "is_stale": stale_minutes is None or stale_minutes > 60,
            "stale_minutes": stale_minutes,
            "raw_chain_snapshots": raw_chain_snapshots,
            "feature_snapshots": feature_snapshots,
            "setup_candidates": setup_candidates,
            "errors_last_run": 0,  # Placeholder: could be tracked in a metrics table
        }
    except Exception as exc:
        logger.warning("Failed to gather system status: %s", exc)
        return {
            "cache_only": settings.cache_only,
            "marketdata_enabled": settings.marketdata_enabled and not settings.cache_only,
            "last_snapshot_ts": None,
            "is_stale": True,
            "stale_minutes": None,
            "raw_chain_snapshots": 0,
            "feature_snapshots": 0,
            "setup_candidates": 0,
            "errors_last_run": 0,
        }


def get_dashboard() -> dict[str, Any]:
    """Assemble unified dashboard response.
    
    Response schema:
    {
      "generated_at": ISO8601,
      "watchlist": [str],
      "rankings": [ RankedItem... ],
      "macro_brief": { BriefResult subset or null },
      "rs_candidates": [ ... ],
      "system_status": { ... }
    }
    
    - No external API calls
    - No ingestion triggered
    - Reads from DB and saved files only
    - Fast (<100ms on local DB)
    """
    
    watchlist = settings.watchlist
    
    # Get rankings (degraded list if needed)
    rankings_items, _ = get_rankings_unified(limit=5, min_confidence=0.3)
    rankings = [asdict(item) for item in rankings_items]
    
    # Load latest macro brief
    brief_data = load_latest_brief()
    macro_brief = None
    if brief_data:
        macro_brief = {
            "generated_at": brief_data.get("generated_at"),
            "regime": brief_data.get("regime", "neutral"),
            "risk_level": brief_data.get("risk_level", "medium"),
            "regime_30d_call": brief_data.get("regime_30d_call", ""),
            "sector_leaders": brief_data.get("sector_leaders", []),
            "sector_laggards": brief_data.get("sector_laggards", []),
            "data_quality": brief_data.get("error") or "high",
            "summary": _brief_summary(brief_data),
            "tags": brief_data.get("narrative_tags", []),
        }
    
    # System status
    conn = None
    try:
        conn = get_connection(settings.db_path)
        bootstrap_schema(conn, SCHEMA_PATH)
        system_status = get_system_status(conn)
    except Exception as exc:
        logger.warning("Failed to get system status: %s", exc)
        system_status = {
            "last_snapshot_ts": None,
            "is_stale": True,
            "stale_minutes": None,
            "raw_chain_snapshots": 0,
            "feature_snapshots": 0,
            "setup_candidates": 0,
            "errors_last_run": 0,
        }
    finally:
        if conn:
            conn.close()
    
    # RS candidates (placeholder for future integration)
    rs_candidates = []
    
    return {
        "generated_at": utc_now_iso(),
        "watchlist": watchlist,
        "rankings": rankings,
        "macro_brief": macro_brief,
        "rs_candidates": rs_candidates,
        "system_status": system_status,
    }


def _truncate_text(text: str, max_chars: int = 500) -> str:
    """Truncate text to max_chars, trying to preserve sentence boundaries."""
    if len(text) <= max_chars:
        return text
    
    truncated = text[:max_chars]
    # Try to truncate at last sentence boundary
    last_period = truncated.rfind(".")
    if last_period > max_chars * 0.7:  # Only use if it's not too early
        return truncated[:last_period+1]
    
    return truncated + "..."


def _brief_summary(brief_data: dict[str, Any]) -> str:
    exec_summary = str(brief_data.get("exec_summary") or "").strip()
    if exec_summary:
        return exec_summary
    return _first_sentences(str(brief_data.get("full_text", "")), 2)


def _first_sentences(text: str, count: int = 2) -> str:
    import re
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if p.strip()]
    return " ".join(parts[:count])


def _stale_minutes(last_snapshot_ts: str | None) -> int | None:
    if not last_snapshot_ts:
        return None
    try:
        ts = datetime.fromisoformat(last_snapshot_ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - ts.astimezone(timezone.utc)
        return max(0, int(diff.total_seconds() // 60))
    except Exception:
        return None
