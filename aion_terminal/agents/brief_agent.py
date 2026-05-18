from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from aion_terminal.agents.prompts import MACRO_BRIEF_SYSTEM_PROMPT
from aion_terminal.app.config import settings
from aion_terminal.models.dto import ManualNarrativeTagRecord
from aion_terminal.storage.repositories import upsert_manual_narrative_tags
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)

BRIEF_MODEL = "gpt-5.4-mini"
TAG_POSTPROCESS_MODEL = "gpt-5.4-nano"
MAX_TOKENS = 8000

WEB_SEARCH_TOOL = {"type": "web_search"}


@dataclass(slots=True)
class BriefResult:
    generated_at: str
    regime: str
    dominant_signal: str
    regime_30d_call: str
    sector_leaders: list[str]
    sector_laggards: list[str]
    narrative_tags: list[dict[str, Any]]
    risk_level: str
    full_text: str
    exec_summary: str
    model: str
    tokens_used: int
    error: str | None = None


def _extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", "")
    if output_text:
        return str(output_text)

    blocks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                blocks.append(str(text))
    return "\n".join(blocks).strip()


def _extract_json_payload(text: str) -> dict[str, Any]:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        payload = json.loads(match.group(1))
    else:
        # Fallback: attempt to parse the last JSON object in raw text
        candidates = re.findall(r"(\{[\s\S]*\})", text)
        if not candidates:
            raise ValueError("json block missing")
        payload = json.loads(candidates[-1])
    
    # Ensure all required fields are present with sensible defaults
    payload.setdefault("regime", "neutral")
    payload.setdefault("regime_axes", {
        "risk": "neutral",
        "inflation": "neutral",
        "liquidity": "neutral",
        "duration": "neutral",
        "credit": "benign"
    })
    payload.setdefault("dominant_signal", "Market conditions remain fluid pending clarification.")
    payload.setdefault("regime_30d_call", "Neutral bias; monitor key levels for directional confirmation.")
    payload.setdefault("sector_leaders", [])
    payload.setdefault("sector_laggards", [])
    payload.setdefault("narrative_tags", [])
    payload.setdefault("risk_level", "medium")
    payload.setdefault("exec_summary", "")
    payload.setdefault("contradictions_resolved", [])
    payload.setdefault("access_failures", [])
    payload.setdefault("data_quality", "medium")
    payload.setdefault("word_count", 0)
    
    return payload




def _first_sentences(text: str, count: int = 2) -> str:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if p.strip()]
    return " ".join(parts[:count])


def _build_exec_summary(payload: dict[str, Any], full_text: str) -> str:
    provided = str(payload.get("exec_summary", "")).strip()
    if provided:
        return provided
    regime = str(payload.get("regime", "neutral")).strip() or "neutral"
    risk = str(payload.get("risk_level", "medium")).strip() or "medium"
    posture = str(payload.get("regime_30d_call", "")).strip()
    if posture:
        return f"Regime: {regime}. Risk level: {risk}. Trading posture: {posture}"
    return _first_sentences(full_text, 2)

def _is_refusal_pattern(text: str) -> bool:
    """Detect refusals AND deferrals — both prevent the brief from shipping."""
    # Only check the first 600 chars; refusals/deferrals come at the top
    head = text[:600].lower()

    refusal_keywords = [
        "i cannot provide", "i apologize", "i'm unable to complete",
        "i am unable to complete", "i refuse", "i cannot write",
        "i'm not able to provide", "i am not able to provide",
    ]
    deferral_keywords = [
        "i can do this, but",
        "i need one more",
        "i need another",
        "one more search pass",
        "one more pass",
        "before i can write",
        "before writing the full brief",
        "if you want, i can proceed",
        "would you like me to",
        "should i proceed",
        "i won't fabricate",
    ]
    return any(keyword in head for keyword in refusal_keywords + deferral_keywords)


def _safe_tokens_used(response: Any) -> int:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return input_tokens + output_tokens


def generate_morning_brief(
    watchlist: list[str] | None = None,
    api_key: str | None = None,
) -> BriefResult:
    key = api_key if api_key is not None else getattr(settings, "openai_api_key", "")
    if not key:
        return BriefResult(
            generated_at=utc_now_iso(),
            regime="neutral",
            dominant_signal="API key not configured",
            regime_30d_call="Unable to generate brief",
            sector_leaders=[],
            sector_laggards=[],
            narrative_tags=[],
            risk_level="medium",
            full_text="",
            exec_summary="",
            model=BRIEF_MODEL,
            tokens_used=0,
            error="OPENAI_API_KEY not configured",
        )

    today = date.today().isoformat()
    universe = watchlist or settings.watchlist
    
    def _call_api(nudge_msg: str = "") -> tuple[str, int]:
        """Make API call and return (full_text, tokens_used)."""
        try:
            from openai import OpenAI

            client = OpenAI(api_key=key)
            user_msg = (
                f"Generate today's morning macro brief. Today's date is {today}. "
                "Search for current data before writing. Be specific and opinionated. "
                "This is a pure macro regime brief — no single-stock calls."
            )
            if nudge_msg:
                user_msg += f"\n\n{nudge_msg}"
            
            response = client.responses.create(
                model=BRIEF_MODEL,
                max_output_tokens=MAX_TOKENS,
                tools=[WEB_SEARCH_TOOL],
                input=[
                    {"role": "system", "content": MACRO_BRIEF_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
            tokens = _safe_tokens_used(response)
            text = _extract_output_text(response)
            return text, tokens
        except Exception as exc:
            logger.exception("Brief generation API call failed")
            raise exc

    try:
        full_text, tokens_used = _call_api()
        logger.info("Morning brief token usage: %s", tokens_used)
        
        # Check for refusal patterns and retry if detected
        if _is_refusal_pattern(full_text):
            logger.warning("Refusal pattern detected in brief response; retrying with nudge")
            try:
                full_text, retry_tokens = _call_api(
                    nudge_msg="Do not ask for permission, do not defer, do not offer choices. "
                        "Ship the full brief in this response. Use the sanctioned uncertainty "
                     "register for gaps ('directional read only,' 'data sparse — inference "
                     "from related signal'). Populate access_failures for any inaccessible "
                      "sources. Return the 10-section narrative, TRADING IMPLICATIONS, and "
                      "valid JSON in ```json fenced block. No preamble — open directly with "
                      "the regime-defining signal sentence."
                )
                tokens_used += retry_tokens
                logger.info("Retry successful; combined token usage: %s", tokens_used)
            except Exception as exc:
                logger.exception("Retry failed; using original output")
        
    except Exception as exc:  # pragma: no cover - network/runtime safeguard
        logger.exception("Brief generation failed")
        return BriefResult(
            generated_at=utc_now_iso(),
            regime="neutral",
            dominant_signal="Generation error",
            regime_30d_call="Unable to generate brief due to API error",
            sector_leaders=[],
            sector_laggards=[],
            narrative_tags=[],
            risk_level="medium",
            full_text="",
            exec_summary="",
            model=BRIEF_MODEL,
            tokens_used=0,
            error=f"api_error: {exc}",
        )

    # Parse JSON payload with defaults
    try:
        payload = _extract_json_payload(full_text)
    except Exception as parse_exc:
        logger.warning("JSON parse failed; using defaults: %s", parse_exc)
        payload = {}

    if not payload or not payload.get("regime"):
        exec_summary = _build_exec_summary({}, full_text)
        return BriefResult(
            generated_at=utc_now_iso(),
            regime=None,
            dominant_signal=None,
            regime_30d_call=None,
            sector_leaders=[],
            sector_laggards=[],
            narrative_tags=[],
            risk_level=None,
            full_text=full_text,
            exec_summary=exec_summary,
            model=BRIEF_MODEL,
            tokens_used=tokens_used,
            error="json_parse_failed",
        )

    # Ensure all fields are present and non-empty
    exec_summary = _build_exec_summary(payload, full_text)

    return BriefResult(
        generated_at=utc_now_iso(),
        regime=str(payload.get("regime", "neutral")) or "neutral",
        dominant_signal=str(payload.get("dominant_signal", "")) or "Market conditions require monitoring",
        regime_30d_call=str(payload.get("regime_30d_call", "")) or "Neutral bias pending confirmation",
        sector_leaders=list(payload.get("sector_leaders") or []),
        sector_laggards=list(payload.get("sector_laggards") or []),
        narrative_tags=list(payload.get("narrative_tags") or []),
        risk_level=str(payload.get("risk_level", "medium")) or "medium",
        full_text=full_text,
        exec_summary=exec_summary,
        model=BRIEF_MODEL,
        tokens_used=tokens_used,
        error=None,
    )


def post_brief_tags(brief: BriefResult, conn) -> int:
    tags = brief.narrative_tags or []
    if not tags:
        return 0

    tag_date = date.today().isoformat()
    records: list[ManualNarrativeTagRecord] = []
    for raw in tags:
        symbol = str(raw.get("symbol", "")).upper().strip()
        tag_key = str(raw.get("tag_key", "")).strip()
        if not symbol or not tag_key:
            continue
        tag = ManualNarrativeTagRecord(
            symbol=symbol,
            tag_date=tag_date,
            tag_key=tag_key,
            tag_value=str(raw.get("tag_value", "")) if raw.get("tag_value") is not None else None,
            context_json=json.dumps({"source": "morning_brief", "generated_at": brief.generated_at}),
        )
        logger.debug("Posting narrative tag: %s", tag)
        records.append(tag)

    return upsert_manual_narrative_tags(conn, records)


def save_brief_to_file(
    brief: BriefResult,
    output_dir: str = "aion_terminal/data/briefs",
) -> str:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    file_path = out_dir / f"{date.today().isoformat()}_morning_brief.json"
    file_path.write_text(json.dumps(asdict(brief), indent=2), encoding="utf-8")
    return str(file_path)
