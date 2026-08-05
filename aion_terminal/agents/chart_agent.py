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


# ---------------------------------------------------------------------------
# Computed chart read (P2-9)
#
# Every field the vision model was asked to read off a chart image — EMA stack,
# trend, RVOL state, compression, support/resistance — is already computed from
# `underlying_bars` in features.technical, exactly and without ambiguity.
# Reading them back out of a rendered picture adds a model call, latency and
# cost in exchange for a less reliable answer, and it cannot be reproduced or
# audited after the fact.
#
# Relative strength comes from the RS scanner's `rs_score` rather than a
# recomputed oscillator: it is the measure this system already maintains, so
# duplicating it with a second indicator would risk the two disagreeing.
# ---------------------------------------------------------------------------

COMPUTED_MODEL = "computed:technical"


def _rvol_state(rvol: float) -> str:
    if rvol >= 1.5:
        return "high"
    if rvol < 0.5:
        return "low"
    return "normal"


def _load_rs_score(symbol: str) -> tuple[float | None, float | None]:
    """(rs_score, rs_percentile) from the RS scanner, or (None, None).

    The scanner keeps its own database; its absence before a first scan is a
    normal state, so this reports "unknown" rather than substituting a value.
    """
    try:
        from aion_terminal.storage.rs_repositories import get_rs_connection

        conn = get_rs_connection()
    except Exception:
        return None, None
    try:
        row = conn.execute(
            "SELECT rs_score, rs_percentile FROM rs_scan_results WHERE UPPER(ticker) = ? "
            "ORDER BY scan_ts DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
    except Exception:
        logger.debug("rs_scan_results unavailable for %s", symbol)
        return None, None
    finally:
        conn.close()
    if not row:
        return None, None
    try:
        return float(row[0]), (float(row[1]) if row[1] is not None else None)
    except (TypeError, ValueError):
        return None, None


def analyze_chart_computed(
    conn,
    symbol: str,
    timeframe: str = "D",
    dealer_context: dict[str, Any] | None = None,
) -> ChartAnalysisResult:
    """Derive the chart read from bars instead of from an image.

    No vision model, no API key, no network. Deterministic and reproducible:
    the same bars always yield the same answer.
    """
    from aion_terminal.features.technical import build_technical_features
    from aion_terminal.models.dto import UnderlyingBarRecord
    from aion_terminal.models.enums import EMAStack, TrendState
    from aion_terminal.utils.bars import load_recent_daily_bars
    from aion_terminal.utils.math_utils import as_float

    symbol = symbol.upper()
    warnings: list[str] = []

    rows = load_recent_daily_bars(conn, symbol, 60)
    bars = [
        UnderlyingBarRecord(
            symbol=r["symbol"], timeframe=r["timeframe"], bar_ts=r["bar_ts"],
            open=as_float(r["open"]), high=as_float(r["high"]), low=as_float(r["low"]),
            close=as_float(r["close"]), volume=r["volume"], vwap=as_float(r["vwap"]),
        )
        for r in rows
    ]

    if not bars:
        return ChartAnalysisResult(
            symbol=symbol, timeframe=timeframe, bias="neutral", setup_score=0,
            ema_stack="mixed", trend="neutral", rvol_state="unknown", rsi_level=None,
            rsi_divergence="not_computed", compressed=False, gap_fills_visible=[],
            setup_class="none", invalidation_note="no bars available",
            invalidation_price_estimate=None, warnings=["no_bars"], brief="",
            dealer_context=dealer_context or {}, generated_at=utc_now_iso(),
            model=COMPUTED_MODEL, error=None,
        )

    if len(bars) < 55:
        warnings.append(f"only_{len(bars)}_bars")

    features, state = build_technical_features(bars, timeframe)
    rs_score, rs_percentile = _load_rs_score(symbol)
    if rs_score is None:
        warnings.append("rs_score_unavailable")

    # Direction follows the EMA stack, with trend as the tiebreak. Neither is
    # inferred from the dealer map: that read is directionless (see
    # features.dealer) and treating it as signed is the defect P0-4 removed.
    if features.ema_stack_state == EMAStack.BULLISH.value:
        bias = "bullish"
    elif features.ema_stack_state == EMAStack.BEARISH.value:
        bias = "bearish"
    elif features.trend_state in TrendState.bullish_values():
        bias = "bullish"
    elif features.trend_state in TrendState.bearish_values():
        bias = "bearish"
    else:
        bias = "neutral"

    # Score is a plain count of confirming conditions out of five, reported on
    # a 0-100 scale. It is a summary of what was observed, not a probability —
    # nothing here has been calibrated against outcomes.
    confirmations = 0
    if features.ema_stack_state in (EMAStack.BULLISH.value, EMAStack.BEARISH.value):
        confirmations += 1
    if features.trend_state != TrendState.NEUTRAL.value:
        confirmations += 1
    if state.high_rvol:
        confirmations += 1
    if features.compressed:
        confirmations += 1
    if rs_percentile is not None and rs_percentile >= 70.0:
        confirmations += 1
    setup_score = int(round(confirmations / 5 * 100))

    if bias == "bullish":
        setup_class = "pullback_into_support" if state.recovering_from_pullback else "momentum_continuation"
        invalidation = features.support or None
        invalidation_note = "close below computed support"
    elif bias == "bearish":
        setup_class = "momentum_continuation"
        invalidation = features.resistance or None
        invalidation_note = "close above computed resistance"
    else:
        setup_class = "none"
        invalidation = None
        invalidation_note = "no directional thesis"

    if rs_score is None:
        rs_text = "RS unavailable"
    else:
        rs_text = f"RS {rs_score:.1f}" + (f" (p{rs_percentile:.0f})" if rs_percentile is not None else "")

    brief = (
        f"{symbol} {timeframe}: {features.ema_stack_state}, {features.trend_state}, "
        f"RVOL {features.rvol:.2f} ({_rvol_state(features.rvol)}), "
        f"{'compressed' if features.compressed else 'not compressed'}, "
        f"{rs_text}. "
        f"Computed from {len(bars)} daily bars."
    )

    return ChartAnalysisResult(
        symbol=symbol,
        timeframe=timeframe,
        bias=bias,
        setup_score=setup_score,
        ema_stack=features.ema_stack_state,
        trend=features.trend_state,
        rvol_state=_rvol_state(features.rvol),
        # This system measures relative strength with the RS scanner's score,
        # not an oscillator. Left explicitly uncomputed rather than filled with
        # a second, potentially disagreeing indicator.
        rsi_level=None,
        rsi_divergence="not_computed",
        compressed=features.compressed,
        gap_fills_visible=[],
        setup_class=setup_class,
        invalidation_note=invalidation_note,
        invalidation_price_estimate=invalidation,
        warnings=warnings,
        brief=brief,
        dealer_context={
            **(dealer_context or {}),
            "rs_score": rs_score,
            "rs_percentile": rs_percentile,
            "vwap": features.vwap,
            "atr": features.atr,
            "support": features.support,
            "resistance": features.resistance,
        },
        generated_at=utc_now_iso(),
        model=COMPUTED_MODEL,
        error=None,
    )


def save_chart_analysis(conn, result: ChartAnalysisResult) -> str | None:
    """Persist a chart analysis. Returns the row id, or None on failure.

    Failures are logged rather than swallowed: a lost analysis is a gap in the
    record this system is meant to be audited against.
    """
    import json as _json
    import sqlite3 as _sqlite3
    import uuid as _uuid

    analysis_id = str(_uuid.uuid4())
    try:
        conn.execute(
            """
            INSERT INTO chart_analyses (
                analysis_id, generated_at, symbol, timeframe, source, bias, setup_score,
                ema_stack, trend, rvol_state, compressed, setup_class, invalidation_price,
                invalidation_note, brief, warnings_json, dealer_context_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                analysis_id, result.generated_at, result.symbol, result.timeframe,
                result.model, result.bias, result.setup_score, result.ema_stack,
                result.trend, result.rvol_state, int(bool(result.compressed)),
                result.setup_class, result.invalidation_price_estimate,
                result.invalidation_note, result.brief,
                _json.dumps(result.warnings), _json.dumps(result.dealer_context, default=str),
                result.generated_at,
            ),
        )
        conn.commit()
        return analysis_id
    except _sqlite3.Error:
        logger.exception("chart analysis persistence failed symbol=%s", result.symbol)
        return None
