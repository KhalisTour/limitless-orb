from __future__ import annotations

from fastapi import APIRouter, HTTPException

from aion_terminal.backtests.outcomes import (
    query_expectancy_by_bias,
    query_expectancy_by_moneyness,
    query_expectancy_by_setup_class,
    query_expectancy_by_symbol,
)
from aion_terminal.app.config import settings
from aion_terminal.storage.db import bootstrap_schema, get_connection

router = APIRouter(tags=["backtests"])
SCHEMA_PATH = "aion_terminal/storage/schema.sql"


@router.get("/backtests/health")
def backtests_health():
    return {"ok": True}


@router.get("/backtests/expectancy")
def backtests_expectancy(group_by: str = "setup_class"):
    valid = {"setup_class", "symbol", "bias", "moneyness"}
    if group_by not in valid:
        raise HTTPException(status_code=422, detail="group_by must be one of setup_class, symbol, bias, moneyness")

    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        if group_by == "setup_class":
            results = query_expectancy_by_setup_class(conn)
        elif group_by == "symbol":
            results = query_expectancy_by_symbol(conn)
        elif group_by == "bias":
            results = query_expectancy_by_bias(conn)
        else:
            results = query_expectancy_by_moneyness(conn)
        return {"group_by": group_by, "results": results}
    finally:
        conn.close()
