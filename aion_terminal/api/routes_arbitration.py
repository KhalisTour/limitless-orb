from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from aion_terminal.app.config import settings
from aion_terminal.arbitration.service import (
    _arb_to_json,
    get_arbitration,
    get_arbitration_candidates,
    rebuild_adaptive_layer,
)
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.utils.time_utils import utc_now_iso

router = APIRouter(prefix="/arbitration", tags=["arbitration"])

SCHEMA_PATH = "aion_terminal/storage/schema.sql"


@router.get("/candidates")
async def get_arbitration_candidates_route(limit: int = 20):
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        return get_arbitration_candidates(conn, limit=limit)
    finally:
        conn.close()


@router.post("/rebuild")
async def rebuild_arbitration_adaptive(background_tasks: BackgroundTasks):
    def _job():
        conn = get_connection(settings.db_path)
        bootstrap_schema(conn, SCHEMA_PATH)
        try:
            rebuild_adaptive_layer(conn)
        finally:
            conn.close()

    background_tasks.add_task(_job)
    return {"status": "rebuild_started", "timestamp": utc_now_iso()}


@router.get("/{symbol}")
async def get_symbol_arbitration(symbol: str):
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        arb = get_arbitration(symbol.upper(), conn)
        return _arb_to_json(arb)
    finally:
        conn.close()
