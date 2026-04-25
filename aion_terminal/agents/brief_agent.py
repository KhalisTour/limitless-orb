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
MAX_TOKENS = 4096

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
    if not match:
        raise ValueError("json block missing")
    return json.loads(match.group(1))


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
            regime="",
            dominant_signal="",
            regime_30d_call="",
            sector_leaders=[],
            sector_laggards=[],
            narrative_tags=[],
            risk_level="",
            full_text="",
            model=BRIEF_MODEL,
            tokens_used=0,
            error="OPENAI_API_KEY not configured",
        )

    today = date.today().isoformat()
    universe = watchlist or settings.watchlist
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        response = client.responses.create(
            model=BRIEF_MODEL,
            max_output_tokens=MAX_TOKENS,
            tools=[WEB_SEARCH_TOOL],
            input=[
                {"role": "system", "content": MACRO_BRIEF_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Generate today's morning macro brief. Today's date is {today}. "
                        f"My trading universe is: {universe}. "
                        "Search for current data before writing. Be specific and opinionated."
                    ),
                },
            ],
        )
    except Exception as exc:  # pragma: no cover - network/runtime safeguard
        logger.exception("Brief generation failed")
        return BriefResult(
            generated_at=utc_now_iso(),
            regime="",
            dominant_signal="",
            regime_30d_call="",
            sector_leaders=[],
            sector_laggards=[],
            narrative_tags=[],
            risk_level="",
            full_text="",
            model=BRIEF_MODEL,
            tokens_used=0,
            error=f"api_error: {exc}",
        )

    tokens_used = _safe_tokens_used(response)
    logger.info("Morning brief token usage: %s", tokens_used)

    full_text = _extract_output_text(response)
    try:
        payload = _extract_json_payload(full_text)
    except Exception:
        return BriefResult(
            generated_at=utc_now_iso(),
            regime="",
            dominant_signal="",
            regime_30d_call="",
            sector_leaders=[],
            sector_laggards=[],
            narrative_tags=[],
            risk_level="",
            full_text=full_text,
            model=BRIEF_MODEL,
            tokens_used=tokens_used,
            error="json_parse_failed",
        )

    return BriefResult(
        generated_at=utc_now_iso(),
        regime=str(payload.get("regime", "")),
        dominant_signal=str(payload.get("dominant_signal", "")),
        regime_30d_call=str(payload.get("regime_30d_call", "")),
        sector_leaders=[str(v) for v in (payload.get("sector_leaders") or [])],
        sector_laggards=[str(v) for v in (payload.get("sector_laggards") or [])],
        narrative_tags=list(payload.get("narrative_tags") or []),
        risk_level=str(payload.get("risk_level", "")),
        full_text=full_text,
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
