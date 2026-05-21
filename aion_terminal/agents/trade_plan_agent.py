from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from aion_terminal.agents.prompts import TRADE_PLAN_SYSTEM_PROMPT_V3
from aion_terminal.app.config import settings
from aion_terminal.decision_engine.service import run_decision_engine
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)
TRADE_PLAN_MODEL = "gpt-5.4-mini"
MAX_TOKENS = 4096


@dataclass(slots=True)
class TradePlanResult:
    generated_at: str
    symbol: str
    decision: str
    bias: str
    confidence: float | None
    confidence_label: str | None
    narrative: str
    json_plan: dict
    decision_engine: dict
    model: str
    tokens_used: int
    error: str | None = None


def _extract_output_text(response: Any) -> str:
    if getattr(response, "output_text", ""):
        return str(response.output_text)
    blocks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            txt = getattr(content, "text", None)
            if txt:
                blocks.append(str(txt))
    return "\n".join(blocks).strip()


def _safe_tokens(response: Any) -> int:
    u = getattr(response, "usage", None)
    return int(getattr(u, "input_tokens", 0) or 0) + int(getattr(u, "output_tokens", 0) or 0)


def _extract_json_plan(raw: str) -> tuple[str, dict]:
    narrative = raw
    if "JSON_PLAN" in raw:
        parts = raw.split("JSON_PLAN", 1)
        narrative = parts[0].strip()
        tail = parts[1]
    else:
        tail = raw
    m = re.search(r"```json\s*(\{.*\})\s*```", tail, re.DOTALL)
    if m:
        return narrative, json.loads(m.group(1))
    m2 = re.search(r"(\{.*\})", tail, re.DOTALL)
    if m2:
        return narrative, json.loads(m2.group(1))
    raise ValueError("json_plan_missing")


def generate_trade_plan(symbol: str, user_requested_bias: str | None = None, user_requested_style: str | None = None, user_thesis_text: str | None = None, account_buying_power: float | None = 5000, portfolio_value: float | None = None, cash_account: bool = True, rankings_payload: dict | None = None, contract_recommendations: dict | None = None, macro_context: dict | None = None, chart_context: dict | None = None, current_positions: list[dict] | None = None, session_prior_trades: list[dict] | None = None, user_historical_outcomes: dict | None = None, weekly_pattern_summary: str | None = None, arbitration_result: dict | None = None, api_key: str | None = None) -> TradePlanResult:
    key = api_key if api_key is not None else getattr(settings, "openai_api_key", "")
    if not key:
        return TradePlanResult(utc_now_iso(), symbol.upper(), "", "", None, None, "", {}, {}, TRADE_PLAN_MODEL, 0, "OPENAI_API_KEY not configured")
    payload = {"symbol": symbol.upper(), "user_requested_bias": user_requested_bias, "user_requested_style": user_requested_style, "user_thesis_text": user_thesis_text, "account_buying_power": account_buying_power, "portfolio_value": portfolio_value, "cash_account": cash_account, "rankings_payload": rankings_payload, "contract_recommendations": contract_recommendations, "macro_context": macro_context, "chart_context": chart_context, "current_positions": current_positions or [], "session_prior_trades": session_prior_trades or [], "user_historical_outcomes": user_historical_outcomes or {}, "weekly_pattern_summary": weekly_pattern_summary, "arbitration_result": arbitration_result}
    decision_engine_output = run_decision_engine(symbol, payload)
    payload["decision_engine"] = decision_engine_output
    system_prompt = TRADE_PLAN_SYSTEM_PROMPT_V3
    if arbitration_result:
        arb_context = (
            "\n\nARBITRATION DECISION (deterministic — do not override):\n"
            f"Decision: {arbitration_result.get('arb_decision')}\n"
            f"Confidence: {arbitration_result.get('confidence')}\n"
            f"Approved contract role: {arbitration_result.get('approved_contract_role')}\n"
            f"Sizing modifier: {arbitration_result.get('sizing_modifier')}\n"
            f"Conflicts: {arbitration_result.get('conflicts', [])}\n"
            f"Required trigger: {arbitration_result.get('required_trigger')}\n"
            f"Kill switch: {arbitration_result.get('kill_switch')}\n"
            f"Hold policy: {arbitration_result.get('hold_policy')}\n"
            f"Warnings: {arbitration_result.get('warnings', [])}\n\n"
            "Your job is to explain WHY the arbiter reached this decision and produce "
            "an execution plan consistent with it. Do NOT override the arb_decision. "
            "Do NOT recommend a contract role different from approved_contract_role.\n"
        )
        system_prompt = arb_context + system_prompt
    try:
        from openai import OpenAI

        llm_payload = json.dumps(payload, default=str)
        response = OpenAI(api_key=key).responses.create(model=TRADE_PLAN_MODEL, max_output_tokens=MAX_TOKENS, input=[{"role": "system", "content": system_prompt}, {"role": "user", "content": llm_payload}])
        raw = _extract_output_text(response)
        tokens = _safe_tokens(response)
        logger.info("Trade plan token usage: %s", tokens)
        narrative, json_plan = _extract_json_plan(raw)
        json_plan.setdefault("decision_engine", {k: decision_engine_output.get(k) for k in ["state", "ev_score", "p_touch_s1", "p_touch_s3", "action_bias"]})
        return TradePlanResult(utc_now_iso(), symbol.upper(), str(json_plan.get("decision", "")), str(json_plan.get("bias", "")), json_plan.get("confidence"), json_plan.get("confidence_label"), narrative or raw, json_plan, decision_engine_output, TRADE_PLAN_MODEL, tokens, None)
    except Exception as exc:
        text = locals().get("raw", "")
        err = "json_parse_failed" if "json_plan_missing" in str(exc) or "Expecting" in str(exc) else f"api_error: {exc}"
        return TradePlanResult(utc_now_iso(), symbol.upper(), "", "", None, None, text, {}, decision_engine_output, TRADE_PLAN_MODEL, 0, err)
