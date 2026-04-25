from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from aion_terminal.agents.brief_agent import generate_morning_brief, post_brief_tags, save_brief_to_file
from aion_terminal.agents.chart_agent import analyze_chart_from_bytes
from aion_terminal.app.config import settings
from aion_terminal.services.ranking_service import rank_symbol
from aion_terminal.storage.db import bootstrap_schema, get_connection

router = APIRouter(tags=["agents"])
SCHEMA_PATH = "aion_terminal/storage/schema.sql"
BRIEF_DIR = Path("aion_terminal/data/briefs")
CHART_HISTORY: deque[dict[str, Any]] = deque(maxlen=50)


class BriefRequest(BaseModel):
    watchlist: list[str] | None = None
    post_tags: bool = True


@router.post("/agents/brief")
async def create_morning_brief(payload: BriefRequest):
    brief = await asyncio.to_thread(generate_morning_brief, payload.watchlist)

    tags_posted = 0
    if payload.post_tags and not brief.error:
        conn = get_connection(settings.db_path)
        bootstrap_schema(conn, SCHEMA_PATH)
        try:
            tags_posted = await asyncio.to_thread(post_brief_tags, brief, conn)
        finally:
            conn.close()

    file_path = await asyncio.to_thread(save_brief_to_file, brief)
    response = asdict(brief)
    response["tags_posted"] = tags_posted
    response["saved_to"] = file_path
    return response


@router.get("/agents/brief/latest")
def get_latest_brief():
    if not BRIEF_DIR.exists():
        return {"error": "no brief found"}
    files = sorted(BRIEF_DIR.glob("*_morning_brief.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"error": "no brief found"}
    return json.loads(files[0].read_text(encoding="utf-8"))


def _dealer_context_for_symbol(symbol: str) -> dict[str, Any]:
    ranking = rank_symbol(symbol.upper())
    spot = float(ranking.spot or 0.0)
    call_wall = ranking.call_wall
    put_wall = ranking.put_wall

    call_wall_pct = 0.0
    put_wall_pct = 0.0
    if spot and call_wall is not None:
        call_wall_pct = ((float(call_wall) - spot) / spot) * 100.0
    if spot and put_wall is not None:
        put_wall_pct = ((float(put_wall) - spot) / spot) * 100.0

    return {
        "spot": spot,
        "king_node": ranking.king_node,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "regime": ranking.regime,
        "call_wall_pct": call_wall_pct,
        "put_wall_pct": put_wall_pct,
    }


@router.post("/agents/chart")
async def create_chart_analysis(
    file: UploadFile = File(...),
    symbol: str | None = Form(default=None),
    bias_hint: str | None = Form(default=None),
):
    image_bytes = await file.read()
    media_type = file.content_type or "image/png"
    dealer_context: dict[str, Any] = {}

    if symbol:
        dealer_context = await asyncio.to_thread(_dealer_context_for_symbol, symbol)
        dealer_context["symbol"] = symbol.upper()
    if bias_hint:
        dealer_context["bias_hint"] = bias_hint.lower()

    result = await asyncio.to_thread(
        analyze_chart_from_bytes,
        image_bytes,
        media_type,
        dealer_context,
    )

    payload = asdict(result)
    CHART_HISTORY.appendleft(payload)
    return payload


@router.get("/agents/chart/history")
def get_chart_history(symbol: str | None = None, limit: int = 10):
    limit = max(1, min(limit, 50))
    rows = list(CHART_HISTORY)
    if symbol:
        sym = symbol.upper()
        rows = [r for r in rows if str(r.get("symbol", "")).upper() == sym]
    return rows[:limit]
