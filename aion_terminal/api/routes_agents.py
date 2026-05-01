from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

from aion_terminal.agents.brief_agent import generate_morning_brief, post_brief_tags, save_brief_to_file
from aion_terminal.agents.chart_agent import analyze_chart_from_bytes
from aion_terminal.agents.trade_plan_agent import generate_trade_plan
from aion_terminal.app.config import settings
from aion_terminal.services.ranking_service import rank_symbol
from aion_terminal.storage.db import bootstrap_schema, get_connection

router = APIRouter(tags=["agents"])
SCHEMA_PATH = "aion_terminal/storage/schema.sql"
BRIEF_DIR = Path("aion_terminal/data/briefs")
CHART_HISTORY: deque[dict[str, Any]] = deque(maxlen=50)
TRADE_PLAN_HISTORY: deque[dict[str, Any]] = deque(maxlen=50)


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


class TradePlanRequest(BaseModel):
    symbol: str
    user_requested_bias: str | None = None
    user_requested_style: str | None = None
    user_thesis_text: str | None = None
    account_buying_power: float | None = 5000
    portfolio_value: float | None = None
    cash_account: bool = True
    rankings_payload: dict[str, Any] | None = None
    contract_recommendations: dict[str, Any] | None = None
    macro_context: dict[str, Any] | None = None
    chart_context: dict[str, Any] | None = None
    current_positions: list[dict[str, Any]] = Field(default_factory=list)
    session_prior_trades: list[dict[str, Any]] = Field(default_factory=list)
    user_historical_outcomes: dict[str, Any] = Field(default_factory=dict)
    weekly_pattern_summary: str | None = None


@router.post("/agents/trade-plan")
async def create_trade_plan(payload: TradePlanRequest):
    warnings: list[str] = []
    rankings_payload = payload.rankings_payload
    if rankings_payload is None:
        try:
            ranking = await asyncio.to_thread(rank_symbol, payload.symbol.upper())
            rankings_payload = {"symbol": ranking.symbol, "spot": ranking.spot, "dealer_structure": {"call_wall": ranking.call_wall, "put_wall": ranking.put_wall, "king_node": ranking.king_node}}
        except Exception as exc:
            warnings.append(f"rankings_fetch_failed: {exc}")
    contract_recommendations = payload.contract_recommendations
    if contract_recommendations is None:
        contract_recommendations = (rankings_payload or {}).get("contract_recommendations")
    macro_context = payload.macro_context
    if macro_context is None:
        try:
            files = sorted(BRIEF_DIR.glob("*_morning_brief.json"), key=lambda x: x.stat().st_mtime, reverse=True)
            if files:
                macro_context = json.loads(files[0].read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append(f"macro_context_fetch_failed: {exc}")
    result = await asyncio.to_thread(generate_trade_plan, payload.symbol, payload.user_requested_bias, payload.user_requested_style, payload.user_thesis_text, payload.account_buying_power, payload.portfolio_value, payload.cash_account, rankings_payload, contract_recommendations, macro_context, payload.chart_context, payload.current_positions, payload.session_prior_trades, payload.user_historical_outcomes, payload.weekly_pattern_summary)
    out = asdict(result)
    if warnings:
        out.setdefault("json_plan", {}).setdefault("required_next_data", []).extend(warnings)
    TRADE_PLAN_HISTORY.appendleft(out)
    return out


@router.get("/agents/trade-plan/history")
def get_trade_plan_history(symbol: str | None = None, limit: int = 10):
    limit = max(1, min(limit, 50))
    rows = list(TRADE_PLAN_HISTORY)
    if symbol:
        rows = [r for r in rows if str(r.get("symbol", "")).upper() == symbol.upper()]
    return rows[:limit]
