from __future__ import annotations

from fastapi import APIRouter

from aion_terminal.app.config import settings
from aion_terminal.storage.db import bootstrap_schema, get_connection

router = APIRouter(tags=["tags"])
SCHEMA_PATH = "aion_terminal/storage/schema.sql"


@router.get("/tags/health")
def tags_health():
    return {"ok": True}


@router.get("/tags/{symbol}")
def get_tags(symbol: str):
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        rows = conn.execute(
            """
            SELECT symbol, tag_date, tag_key, tag_value, context_json, created_at, updated_at
            FROM manual_narrative_tags
            WHERE symbol = ?
            ORDER BY tag_date DESC
            """,
            (symbol.upper(),),
        ).fetchall()
        return {"symbol": symbol.upper(), "tags": [dict(r) for r in rows]}
    finally:
        conn.close()
