from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

from aion_terminal.agents.brief_agent import (
    generate_morning_brief,
    post_brief_tags,
    save_brief_to_db,
    save_brief_to_file,
)
from aion_terminal.agents.chart_agent import analyze_chart_from_bytes
from aion_terminal.agents.prompts import TRADE_PLAN_SYSTEM_PROMPT_V3
from aion_terminal.agents.trade_plan_agent import generate_trade_plan
from aion_terminal.app.config import settings
from aion_terminal.services.contract_service import get_contract_recommendation
from aion_terminal.services.ranking_service import infer_bias, rank_symbol
from aion_terminal.storage.db import bootstrap_schema, get_connection

from uuid import uuid4

from aion_terminal.storage.repositories import (
    get_agent_memory_summary,
    insert_trade_plan,
    insert_trade_plan_outcome,
    query_recent_trade_plans,
    query_trade_plan_outcomes,
    upsert_agent_memory_summary,
)
from aion_terminal.utils.time_utils import utc_now_iso

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
    brief_id = None
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        if not brief.error:
            brief_id = await asyncio.to_thread(save_brief_to_db, conn, brief)
        if payload.post_tags and not brief.error:
            tags_posted = await asyncio.to_thread(post_brief_tags, brief, conn)
    finally:
        conn.close()

    file_path = await asyncio.to_thread(save_brief_to_file, brief)
    response = asdict(brief)
    response["tags_posted"] = tags_posted
    response["saved_to"] = file_path
    response["brief_id"] = brief_id
    return response


@router.get("/agents/brief/history")
def get_brief_history(days: int = 14):
    """Return last N days of morning briefs from morning_briefs table."""
    days = max(1, min(int(days), 365))
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        rows = conn.execute(
            """
            SELECT brief_date, generated_at, regime, dominant_signal, risk_level,
                   sector_leaders_json, exec_summary
            FROM morning_briefs
            ORDER BY brief_date DESC
            LIMIT ?
            """,
            (days,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            leaders = json.loads(r["sector_leaders_json"] or "[]")
        except Exception:
            leaders = []
        out.append({
            "brief_date": r["brief_date"],
            "generated_at": r["generated_at"],
            "regime": r["regime"],
            "dominant_signal": r["dominant_signal"],
            "risk_level": r["risk_level"],
            "sector_leaders": leaders,
            "exec_summary": r["exec_summary"],
        })
    return out


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


def build_agent_memory_summary(outcomes: list[dict[str, Any]], recent_plans: list[dict[str, Any]]) -> dict[str, Any]:
    import statistics
    by_plan = {p.get("plan_id"): p for p in recent_plans}
    realized = [float(o.get("realized_return_pct")) for o in outcomes if o.get("realized_return_pct") is not None]
    mfe = [float(o.get("mfe_pct")) for o in outcomes if o.get("mfe_pct") is not None]
    mae = [float(o.get("mae_pct")) for o in outcomes if o.get("mae_pct") is not None]
    win_rate = (sum(1 for v in realized if v > 0) / len(realized)) if realized else 0.0
    exits = [str(o.get("exit_reason")) for o in outcomes if o.get("exit_reason")]
    most_common_exit_reason = max(set(exits), key=exits.count) if exits else None
    symbol_perf: dict[str, list[float]] = {}
    setup_perf: dict[str, list[float]] = {}
    money_perf: dict[str, list[float]] = {}
    warnings: list[str] = []
    for o in outcomes:
        rr = o.get("realized_return_pct")
        if rr is None:
            continue
        rr = float(rr)
        symbol = str(o.get("symbol") or "")
        symbol_perf.setdefault(symbol, []).append(rr)
        plan = by_plan.get(o.get("plan_id"), {})
        setup = str(plan.get("setup_class") or "unknown")
        setup_perf.setdefault(setup, []).append(rr)
        m_bucket = str((json.loads(plan.get("selected_contract_json") or "{}") or {}).get("moneyness_bucket") or "unknown")
        money_perf.setdefault(m_bucket, []).append(rr)
        if o.get("followed_plan") in (0, False) and rr < 0:
            warnings.append("plan_deviation_losses")
    sort_avg = lambda d, rev: sorted(((k, sum(v)/len(v)) for k, v in d.items() if v), key=lambda x: x[1], reverse=rev)
    return {
        "win_rate": win_rate,
        "avg_return_pct": statistics.fmean(realized) if realized else 0.0,
        "avg_mfe_pct": statistics.fmean(mfe) if mfe else 0.0,
        "avg_mae_pct": statistics.fmean(mae) if mae else 0.0,
        "most_common_exit_reason": most_common_exit_reason,
        "best_symbols": [k for k, _ in sort_avg(symbol_perf, True)[:3]],
        "worst_symbols": [k for k, _ in sort_avg(symbol_perf, False)[:3]],
        "best_setup_classes": [k for k, _ in sort_avg(setup_perf, True)[:3]],
        "worst_setup_classes": [k for k, _ in sort_avg(setup_perf, False)[:3]],
        "best_moneyness_buckets": [k for k, _ in sort_avg(money_perf, True)[:3]],
        "common_warnings": sorted(set(warnings)),
        "behavioral_notes": ["favor_plan_adherence" if "plan_deviation_losses" in warnings else "stable_execution"],
    }

def _normalize_contract_fields(contract: dict[str, Any] | None) -> dict[str, Any] | None:
    if not contract:
        return None
    return {
        **contract,
        "contract_symbol": contract.get("contract_symbol"),
        "expiry": contract.get("expiry"),
        "strike": contract.get("strike"),
        "bid": contract.get("bid"),
        "ask": contract.get("ask"),
        "mid": contract.get("mid", contract.get("premium_mid")),
        "delta": contract.get("delta"),
        "gamma": contract.get("gamma"),
        "theta": contract.get("theta"),
        "oi": contract.get("oi", contract.get("open_interest")),
        "volume": contract.get("volume"),
    }


@router.post("/agents/trade-plan")
async def create_trade_plan(payload: TradePlanRequest):
    warnings: list[str] = []
    rankings_payload = payload.rankings_payload
    ranking = None
    if rankings_payload is None:
        try:
            ranking = await asyncio.to_thread(rank_symbol, payload.symbol.upper())
            top_setup = ranking.signals[0] if ranking.signals else None
            rankings_payload = {
                "symbol": ranking.symbol,
                "spot": ranking.spot,
                "dealer_structure": {
                    "call_wall": ranking.call_wall,
                    "put_wall": ranking.put_wall,
                    "king_node": ranking.king_node,
                },
                "top_ranked_setup": top_setup,
            }
        except Exception as exc:
            warnings.append(f"rankings_fetch_failed: {exc}")
    contract_recommendations = payload.contract_recommendations
    if contract_recommendations is None:
        if ranking is not None:
            contract_recommendations = {
                "best": _normalize_contract_fields(ranking.best_contract),
                "safer": _normalize_contract_fields(ranking.safer_contract),
                "convex": _normalize_contract_fields(ranking.convex_contract),
            }
        if not contract_recommendations or not any(contract_recommendations.values()):
            try:
                setup_bias = ((rankings_payload or {}).get("top_ranked_setup") or {}).get("bias")
                inferred_bias, inferred_warnings = infer_bias(
                    explicit_bias=payload.user_requested_bias,
                    setup_bias=setup_bias,
                    ranking_bias=((rankings_payload or {}).get("bias")),
                )
                contract_scored = await asyncio.to_thread(
                    get_contract_recommendation,
                    payload.symbol.upper(),
                    inferred_bias,
                    0,
                    21,
                )
                contract_recommendations = {
                    "best": _normalize_contract_fields(contract_scored.get("best")),
                    "safer": _normalize_contract_fields(contract_scored.get("safer")),
                    "convex": _normalize_contract_fields(contract_scored.get("convex")),
                    "all_scored": contract_scored.get("all_scored", [])[:10],
                    "warnings": [*contract_scored.get("warnings", []), *inferred_warnings],
                }
            except Exception as exc:
                warnings.append(f"contracts_fetch_failed: {exc}")
    macro_context = payload.macro_context
    if macro_context is None:
        try:
            files = sorted(BRIEF_DIR.glob("*_morning_brief.json"), key=lambda x: x.stat().st_mtime, reverse=True)
            if files:
                macro_context = json.loads(files[0].read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append(f"macro_context_fetch_failed: {exc}")
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        global_memory = get_agent_memory_summary(conn, "global")
        symbol_outcomes = query_trade_plan_outcomes(conn, symbol=payload.symbol, limit=30)
    finally:
        conn.close()
    memory_payload = payload.user_historical_outcomes or {}
    if global_memory:
        memory_payload = {**memory_payload, "global_summary": json.loads(global_memory.get("summary_json") or "{}")}
    if symbol_outcomes:
        memory_payload = {**memory_payload, "symbol_recent_outcomes": symbol_outcomes}

    arbitration_result = None
    try:
        from aion_terminal.arbitration.service import get_arbitration, _arb_to_json
        arb_conn = get_connection(settings.db_path)
        bootstrap_schema(arb_conn, SCHEMA_PATH)
        try:
            arb = await asyncio.to_thread(get_arbitration, payload.symbol.upper(), arb_conn)
            arbitration_result = _arb_to_json(arb)
        finally:
            arb_conn.close()
    except Exception as arb_exc:
        warnings.append(f"arbitration_unavailable: {arb_exc}")

    result = await asyncio.to_thread(generate_trade_plan, payload.symbol, payload.user_requested_bias, payload.user_requested_style, payload.user_thesis_text, payload.account_buying_power, payload.portfolio_value, payload.cash_account, rankings_payload, contract_recommendations, macro_context, payload.chart_context, payload.current_positions, payload.session_prior_trades, memory_payload, payload.weekly_pattern_summary, arbitration_result)
    if isinstance(result, dict):
        out = result
    else:
        try:
            from dataclasses import asdict as _asdict
            out = _asdict(result)
        except TypeError:
            out = result.__dict__ if hasattr(result, "__dict__") else {}
    if warnings:
        out.setdefault("json_plan", {}).setdefault("required_next_data", []).extend(warnings)
    plan_id = str(uuid4())
    out["plan_id"] = plan_id
    selected_contract = ((out.get("json_plan") or {}).get("best_plan") or {}).get("selected_contract") or (contract_recommendations or {}).get("best")
    context_payload = {"rankings_payload": rankings_payload, "contract_recommendations": contract_recommendations, "macro_context": macro_context, "chart_context": payload.chart_context}
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        insert_trade_plan(conn, {
            "plan_id": plan_id,
            "generated_at": out.get("generated_at") or utc_now_iso(),
            "symbol": payload.symbol.upper(),
            "bias": out.get("bias"),
            "decision": out.get("decision") or (out.get("json_plan") or {}).get("decision"),
            "confidence": out.get("confidence"),
            "confidence_label": out.get("confidence_label"),
            "setup_class": ((out.get("json_plan") or {}).get("best_plan") or {}).get("setup_class"),
            "selected_contract_symbol": (selected_contract or {}).get("contract_symbol"),
            "selected_contract_json": json.dumps(selected_contract or {}, default=str),
            "decision_engine_json": json.dumps(out.get("decision_engine") or {}, default=str),
            "json_plan": json.dumps(out.get("json_plan") or {}, default=str),
            "narrative": out.get("narrative") or "",
            "context_json": json.dumps(context_payload, default=str),
            "model": out.get("model"),
            "tokens_used": out.get("tokens_used"),
        })
    finally:
        conn.close()
    TRADE_PLAN_HISTORY.appendleft(out)
    return out


@router.get("/agents/trade-plan/history")
def get_trade_plan_history(symbol: str | None = None, limit: int = 20):
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        rows = query_recent_trade_plans(conn, symbol=symbol, limit=limit)
    finally:
        conn.close()
    return rows




class TradePlanOutcomeRequest(BaseModel):
    contract_symbol: str | None = None
    entry_ts: str | None = None
    entry_price: float | None = None
    exit_ts: str | None = None
    exit_price: float | None = None
    realized_return_pct: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    exit_reason: str | None = None
    followed_plan: bool | None = None
    notes: str | None = None


@router.post("/agents/trade-plan/{plan_id}/outcome")
def create_trade_plan_outcome(plan_id: str, payload: TradePlanOutcomeRequest):
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        plans = query_recent_trade_plans(conn, limit=500)
        plan = next((p for p in plans if p.get("plan_id") == plan_id), None)
        symbol = str((plan or {}).get("symbol") or "")
        outcome = {"outcome_id": str(uuid4()), "plan_id": plan_id, "symbol": symbol, **payload.model_dump(), "created_at": utc_now_iso()}
        insert_trade_plan_outcome(conn, outcome)
    finally:
        conn.close()
    return outcome


@router.get("/agents/trade-plan/outcomes")
def get_trade_plan_outcomes(symbol: str | None = None, limit: int = 100):
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        return query_trade_plan_outcomes(conn, symbol=symbol, limit=limit)
    finally:
        conn.close()


@router.get("/agents/memory/summary")
def get_memory_summary(scope: str = "global"):
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        row = get_agent_memory_summary(conn, scope)
    finally:
        conn.close()
    return row or {"scope": scope, "summary_json": "{}"}


@router.post("/agents/memory/rebuild")
def rebuild_memory_summary():
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        outcomes = query_trade_plan_outcomes(conn, limit=500)
        plans = query_recent_trade_plans(conn, limit=500)
        summary = build_agent_memory_summary(outcomes, plans)
        upsert_agent_memory_summary(conn, "global", json.dumps(summary, default=str))
    finally:
        conn.close()
    return summary

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    context: dict[str, Any] | None = None
    max_tokens: int = 1500


def _run_trade_plan_chat(
    messages: list[dict[str, Any]],
    context: dict[str, Any] | None,
    max_tokens: int,
) -> dict[str, Any]:
    api_key = getattr(settings, "anthropic_api_key", "") or ""
    if not api_key:
        return {"reply": "", "error": "ANTHROPIC_API_KEY not configured"}

    try:
        from anthropic import Anthropic
    except Exception as exc:
        return {"reply": "", "error": f"anthropic_import_failed: {exc}"}

    system_prompt = TRADE_PLAN_SYSTEM_PROMPT_V3
    if context:
        try:
            ctx_str = json.dumps(context, indent=2, default=str)
            system_prompt = (
                f"{system_prompt}\n\nCurrent dealer context:\n{ctx_str}"
            )
        except Exception:
            pass

    client = Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )
    except Exception as exc:
        return {"reply": "", "error": f"anthropic_call_failed: {exc}"}

    text_blocks: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            text_blocks.append(str(text))
    reply = "\n".join(text_blocks).strip()
    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    return {
        "reply": reply,
        "model": getattr(response, "model", "claude-sonnet-4-5"),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


@router.post("/agents/chat")
async def agent_chat(payload: ChatRequest):
    """Lightweight conversational wrapper for the Trade Plan agent."""
    messages = [{"role": m.role, "content": m.content} for m in payload.messages]
    if not messages:
        return {"reply": "", "error": "messages required"}
    result = await asyncio.to_thread(
        _run_trade_plan_chat,
        messages,
        payload.context,
        payload.max_tokens,
    )
    return result
