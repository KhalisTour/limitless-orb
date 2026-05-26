from __future__ import annotations
import re

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aion_terminal.agents.prompts import CHART_ANALYSIS_SYSTEM_PROMPT
from aion_terminal.app.config import settings
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)

CHART_MODEL = "claude-haiku-4-5"
CHART_FALLBACK_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024


@dataclass(slots=True)
class ChartAnalysisResult:
    symbol: str
    timeframe: str
    bias: str
    setup_score: int
    ema_stack: str
    trend: str
    rvol_state: str
    rsi_level: float | None
    rsi_divergence: str
    compressed: bool
    gap_fills_visible: list[str]
    setup_class: str
    invalidation_note: str
    invalidation_price_estimate: float | None
    warnings: list[str]
    brief: str
    dealer_context: dict[str, Any]
    generated_at: str
    model: str
    error: str | None = None


def encode_image(image_path: str) -> tuple[str, str]:
    path = Path(image_path)
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    media_type = "image/png"
    if suffix in {".jpg", ".jpeg"}:
        media_type = "image/jpeg"
    encoded = base64.b64encode(raw).decode("utf-8")
    return encoded, media_type


def _dealer_context_string(dealer_context: dict[str, Any] | None) -> str:
    if not dealer_context:
        return "No dealer context provided — analyze chart structure only."

    spot = dealer_context.get("spot")
    king_node = dealer_context.get("king_node")
    call_wall = dealer_context.get("call_wall")
    put_wall = dealer_context.get("put_wall")
    regime = dealer_context.get("regime")
    call_wall_pct = float(dealer_context.get("call_wall_pct") or 0.0)
    put_wall_pct = float(dealer_context.get("put_wall_pct") or 0.0)
    return (
        "Dealer context for this symbol:\n"
        f"Spot: {spot} | King node: {king_node} | Call wall: {call_wall} | Put wall: {put_wall}\n"
        f"Regime: {regime} | Call wall dist: {call_wall_pct:.2f}% | Put wall dist: {put_wall_pct:.2f}%"
    )


def _extract_message_text(resp: Any) -> str:
    content = getattr(resp, "content", None)
    if content is None and isinstance(resp, dict):
        content = resp.get("content", [])
    chunks: list[str] = []
    for item in content or []:
        if isinstance(item, dict):
            text = item.get("text")
        else:
            text = getattr(item, "text", None)
        if text:
            chunks.append(str(text))
    return "\n".join(chunks).strip()


def _extract_usage_tokens(resp: Any) -> int:
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage", {})
    if isinstance(usage, dict):
        return int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
    return int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "output_tokens", 0) or 0)


def _parse_chart_json(raw_text: str, model: str, dealer_context: dict[str, Any]) -> ChartAnalysisResult:
    try:
        # Strip markdown code fences if model wrapped JSON in ```json ... ```
        clean = raw_text.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean)
            clean = re.sub(r"\s*```$", "", clean.strip())
        payload = json.loads(clean)
    except Exception:
        return ChartAnalysisResult(
            symbol="UNKNOWN",
            timeframe="UNKNOWN",
            bias="neutral",
            setup_score=0,
            ema_stack="mixed",
            trend="neutral",
            rvol_state="normal",
            rsi_level=None,
            rsi_divergence="none",
            compressed=False,
            gap_fills_visible=[],
            setup_class="none",
            invalidation_note="Unable to parse model JSON output",
            invalidation_price_estimate=None,
            warnings=[],
            brief=raw_text,
            dealer_context=dealer_context,
            generated_at=utc_now_iso(),
            model=model,
            error="json_parse_failed",
        )

    return ChartAnalysisResult(
        symbol=str(payload.get("symbol", "UNKNOWN")),
        timeframe=str(payload.get("timeframe", "UNKNOWN")),
        bias=str(payload.get("bias", "neutral")),
        setup_score=int(payload.get("setup_score", 0) or 0),
        ema_stack=str(payload.get("ema_stack", "mixed")),
        trend=str(payload.get("trend", "neutral")),
        rvol_state=str(payload.get("rvol_state", "normal")),
        rsi_level=float(payload["rsi_level"]) if payload.get("rsi_level") is not None else None,
        rsi_divergence=str(payload.get("rsi_divergence", "none")),
        compressed=bool(payload.get("compressed", False)),
        gap_fills_visible=[str(x) for x in (payload.get("gap_fills_visible") or [])],
        setup_class=str(payload.get("setup_class", "none")),
        invalidation_note=str(payload.get("invalidation_note", "")),
        invalidation_price_estimate=(
            float(payload["invalidation_price_estimate"]) if payload.get("invalidation_price_estimate") is not None else None
        ),
        warnings=[str(x) for x in (payload.get("warnings") or [])],
        brief=str(payload.get("brief", "")),
        dealer_context=dealer_context,
        generated_at=utc_now_iso(),
        model=model,
        error=None,
    )


def _should_escalate(result: ChartAnalysisResult) -> bool:
    warnings_blob = " ".join(result.warnings).lower()
    return (
        result.setup_score <= 2
        or "unclear" in result.brief.lower()
        or "structural uncertainty" in warnings_blob
        or (result.compressed and result.trend == "neutral")
    )


def _call_anthropic(
    base64_string: str,
    media_type: str,
    dealer_context_string: str,
    model: str,
    api_key: str,
    dealer_context: dict[str, Any],
) -> ChartAnalysisResult:
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=CHART_ANALYSIS_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64_string,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"{dealer_context_string}\n\nAnalyze this chart and return JSON only.",
                    },
                ],
            }
        ],
    )

    tokens_used = _extract_usage_tokens(response)
    logger.info("Chart analysis token usage (%s): %s", model, tokens_used)

    raw = _extract_message_text(response)
    parsed = _parse_chart_json(raw, model=model, dealer_context=dealer_context)
    return parsed


def analyze_chart(
    image_path: str,
    dealer_context: dict[str, Any] | None = None,
    api_key: str | None = None,
) -> ChartAnalysisResult:
    try:
        base64_string, media_type = encode_image(image_path)
    except Exception as exc:
        return ChartAnalysisResult(
            symbol="UNKNOWN",
            timeframe="UNKNOWN",
            bias="neutral",
            setup_score=0,
            ema_stack="mixed",
            trend="neutral",
            rvol_state="normal",
            rsi_level=None,
            rsi_divergence="none",
            compressed=False,
            gap_fills_visible=[],
            setup_class="none",
            invalidation_note="Failed to read image",
            invalidation_price_estimate=None,
            warnings=[str(exc)],
            brief="",
            dealer_context=dealer_context or {},
            generated_at=utc_now_iso(),
            model=CHART_MODEL,
            error=f"image_error: {exc}",
        )
    return analyze_chart_from_bytes(
        image_bytes=base64.b64decode(base64_string.encode("utf-8")),
        media_type=media_type,
        dealer_context=dealer_context,
        api_key=api_key,
    )


def analyze_chart_from_bytes(
    image_bytes: bytes,
    media_type: str = "image/png",
    dealer_context: dict[str, Any] | None = None,
    api_key: str | None = None,
) -> ChartAnalysisResult:
    key = api_key if api_key is not None else getattr(settings, "anthropic_api_key", "")
    if not key:
        return ChartAnalysisResult(
            symbol="UNKNOWN",
            timeframe="UNKNOWN",
            bias="neutral",
            setup_score=0,
            ema_stack="mixed",
            trend="neutral",
            rvol_state="normal",
            rsi_level=None,
            rsi_divergence="none",
            compressed=False,
            gap_fills_visible=[],
            setup_class="none",
            invalidation_note="",
            invalidation_price_estimate=None,
            warnings=[],
            brief="",
            dealer_context=dealer_context or {},
            generated_at=utc_now_iso(),
            model=CHART_MODEL,
            error="ANTHROPIC_API_KEY not configured",
        )

    encoded = base64.b64encode(image_bytes).decode("utf-8")
    dc = dealer_context or {}
    dealer_context_string = _dealer_context_string(dc)

    try:
        primary = _call_anthropic(encoded, media_type, dealer_context_string, CHART_MODEL, key, dc)
    except Exception as exc:  # pragma: no cover
        logger.exception("Chart analysis failed")
        return ChartAnalysisResult(
            symbol="UNKNOWN",
            timeframe="UNKNOWN",
            bias="neutral",
            setup_score=0,
            ema_stack="mixed",
            trend="neutral",
            rvol_state="normal",
            rsi_level=None,
            rsi_divergence="none",
            compressed=False,
            gap_fills_visible=[],
            setup_class="none",
            invalidation_note="",
            invalidation_price_estimate=None,
            warnings=[str(exc)],
            brief="",
            dealer_context=dc,
            generated_at=utc_now_iso(),
            model=CHART_MODEL,
            error=f"api_error: {exc}",
        )

    if not _should_escalate(primary):
        primary.model = CHART_MODEL
        primary.generated_at = utc_now_iso()
        primary.dealer_context = dc
        return primary

    try:
        fallback = _call_anthropic(encoded, media_type, dealer_context_string, CHART_FALLBACK_MODEL, key, dc)
        fallback.model = CHART_FALLBACK_MODEL
        fallback.generated_at = utc_now_iso()
        fallback.dealer_context = dc
        return fallback
    except Exception:  # pragma: no cover
        primary.warnings.append("fallback_failed")
        primary.model = CHART_MODEL
        primary.generated_at = utc_now_iso()
        primary.dealer_context = dc
        return primary
