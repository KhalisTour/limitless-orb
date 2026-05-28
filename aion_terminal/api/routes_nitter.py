from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks

from aion_terminal.app.config import settings
from aion_terminal.services.nitter_service import WATCHED_ACCOUNTS, fetch_all_feeds
from aion_terminal.storage.db import bootstrap_schema, get_connection

router = APIRouter(tags=["feed"])

SCHEMA_PATH = "aion_terminal/storage/schema.sql"

_feed_cache: dict = {}
_CACHE_TTL_SECONDS = 300


@router.get("/feed/x")
async def get_x_feed(background_tasks: BackgroundTasks):
    """Return latest posts from watched X accounts via Nitter RSS.

    Cached for 5 minutes. Never blocks on failure.
    """
    now = datetime.now(timezone.utc).timestamp()
    if _feed_cache.get("data") and (now - _feed_cache.get("ts", 0)) < _CACHE_TTL_SECONDS:
        return _feed_cache["data"]

    try:
        result = fetch_all_feeds(settings.watchlist)
        _feed_cache["data"] = result
        _feed_cache["ts"] = now
        background_tasks.add_task(_persist_feed_tags, result)
        return result
    except Exception:
        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "posts": [],
            "accounts_fetched": 0,
            "instance_used": "none",
            "error": "feed_unavailable",
        }


def _persist_feed_tags(result: dict) -> None:
    """Write watchlist-matching posts to manual_narrative_tags."""
    try:
        conn = get_connection(settings.db_path)
        bootstrap_schema(conn, SCHEMA_PATH)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc).isoformat()
        for post in result.get("posts", []):
            for ticker in post.get("watchlist_match", []):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO manual_narrative_tags
                    (symbol, tag_date, tag_key, tag_value, context_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticker,
                        today,
                        "x_feed_mention",
                        post.get("username"),
                        json.dumps({"text": post.get("text"), "url": post.get("url")}),
                        now,
                        now,
                    ),
                )
        conn.commit()
        conn.close()
    except Exception:
        pass


__all__ = ["router", "WATCHED_ACCOUNTS"]
