from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from aion_terminal.features.technical import TechnicalFeatures, TechnicalState
from aion_terminal.utils.math_utils import as_float

logger = logging.getLogger(__name__)

# Tunable thresholds.
NEAR_WALL_PCT = 3.0
MOMENTUM_WALL_AVOID_PCT = 1.0
FLIP_ZONE_PROXIMITY_PCT = 2.0
DEEP_PULLBACK_PCT = 5.0

WEIGHTS = {
    "dealer_alignment": 0.30,
    "technical_alignment": 0.30,
    "narrative_alignment": 0.20,
    "rvol_confirmation": 0.10,
    "compression_bonus": 0.10,
}

BULLISH_EVENT_TAGS = {
    "bullish_catalyst",
    "sector_rerating_up",
    "policy_tailwind",
    "product_launch",
    "macro_relief",
    "social_rotation_long",
}

BEARISH_EVENT_TAGS = {
    "bearish_catalyst",
    "sector_rerating_down",
    "policy_headwind",
    "regulatory_risk",
    "macro_shock",
    "social_rotation_short",
}


@dataclass(slots=True)
class SetupSignal:
    setup_class: str
    bias: str
    preconditions_met: bool
    confidence_raw: float
    score_components: dict[str, float]
    reason_json: str
    invalidation_price: float | None
    invalidation_rule: str | None
    warnings: list[str]


def _base_components() -> dict[str, float]:
    return {key: 0.0 for key in WEIGHTS}


def _pct_diff(reference: float | None, value: float) -> float:
    if reference is None or reference == 0.0:
        return 10_000.0
    return abs(value - reference) / abs(reference) * 100.0


def _finalize_signal(
    *,
    setup_class: str,
    bias: str,
    preconditions_met: bool,
    components: dict[str, float],
    reasons: list[str],
    invalidation_price: float | None,
    invalidation_rule: str | None,
    warnings: list[str],
) -> SetupSignal:
    confidence = sum(components.values())
    return SetupSignal(
        setup_class=setup_class,
        bias=bias,
        preconditions_met=preconditions_met,
        confidence_raw=confidence,
        score_components=components,
        reason_json=json.dumps(reasons),
        invalidation_price=invalidation_price,
        invalidation_rule=invalidation_rule,
        warnings=warnings,
    )


def evaluate_pullback_into_support_bullish(
    dealer_features: dict[str, Any], technical_features: TechnicalFeatures, technical_state: TechnicalState
) -> SetupSignal:
    components = _base_components()
    reasons: list[str] = []
    warnings: list[str] = []

    spot = as_float(dealer_features.get("spot"))
    put_wall = dealer_features.get("put_wall")
    king_node = as_float(dealer_features.get("king_node"))

    # Price sitting close to structural downside support keeps risk/reward asymmetric.
    near_put_wall = put_wall is not None and put_wall <= spot <= put_wall * (1 + NEAR_WALL_PCT / 100.0)
    near_king = king_node > 0.0 and king_node <= spot <= king_node * (1 + NEAR_WALL_PCT / 100.0)
    dealer_ok = near_put_wall or near_king

    # Pullback recovery or proximity to support suggests demand is re-engaging.
    recovery_or_support = technical_state.recovering_from_pullback or technical_state.near_support

    # Uptrend structure reduces odds of catching a failing knife.
    trend_ok = technical_state.ema_stack == "bullish_stack" or technical_state.trend in {"uptrend", "strong_uptrend"}

    # Avoid taking new longs directly into overhead resistance.
    not_at_resistance = not technical_state.near_resistance

    preconditions_met = dealer_ok and recovery_or_support and trend_ok and not_at_resistance

    if dealer_ok:
        components["dealer_alignment"] = WEIGHTS["dealer_alignment"]
        reasons.append("dealer support context present")
    # Full technical score requires both recovery context and strict bullish stack confirmation.
    technical_scoring_ok = recovery_or_support and technical_state.ema_stack == "bullish_stack"
    if technical_scoring_ok:
        components["technical_alignment"] = WEIGHTS["technical_alignment"]
        reasons.append("technical recovery + bullish stack confirmation")
    if technical_state.high_rvol:
        components["rvol_confirmation"] = WEIGHTS["rvol_confirmation"]
    else:
        warnings.append("low_rvol")

    invalidation_price = as_float(put_wall) if put_wall is not None else as_float(technical_features.support)
    invalidation_rule = "close_below_put_wall" if put_wall is not None else "close_below_support"

    return _finalize_signal(
        setup_class="pullback_into_support",
        bias="bullish",
        preconditions_met=preconditions_met,
        components=components,
        reasons=reasons,
        invalidation_price=invalidation_price,
        invalidation_rule=invalidation_rule,
        warnings=warnings,
    )


def evaluate_pullback_into_support_bearish(
    dealer_features: dict[str, Any], technical_features: TechnicalFeatures, technical_state: TechnicalState
) -> SetupSignal:
    components = _base_components()
    reasons: list[str] = []
    warnings: list[str] = []

    spot = as_float(dealer_features.get("spot"))
    call_wall = dealer_features.get("call_wall")
    king_node = as_float(dealer_features.get("king_node"))

    # Price near overhead structural cap improves short asymmetry.
    near_call_wall = call_wall is not None and call_wall * (1 - NEAR_WALL_PCT / 100.0) <= spot <= call_wall
    near_king_below = king_node > 0.0 and king_node * (1 - NEAR_WALL_PCT / 100.0) <= spot <= king_node
    dealer_ok = near_call_wall or near_king_below

    # Either obvious resistance rejection or deep failed bounce is acceptable bearish trigger.
    rejection_setup = technical_state.near_resistance or technical_state.pullback_depth_pct > DEEP_PULLBACK_PCT

    # Downtrend context improves continuation odds.
    trend_ok = technical_state.ema_stack == "bearish_stack" or technical_state.trend in {"downtrend", "strong_downtrend"}

    preconditions_met = dealer_ok and rejection_setup and trend_ok

    if dealer_ok:
        components["dealer_alignment"] = WEIGHTS["dealer_alignment"]
        reasons.append("dealer resistance context present")
    # Full technical score requires rejection context with strict bearish stack confirmation.
    technical_scoring_ok = rejection_setup and technical_state.ema_stack == "bearish_stack"
    if technical_scoring_ok:
        components["technical_alignment"] = WEIGHTS["technical_alignment"]
        reasons.append("technical rejection + bearish stack confirmation")
    if technical_state.high_rvol:
        components["rvol_confirmation"] = WEIGHTS["rvol_confirmation"]
    else:
        warnings.append("low_rvol")

    invalidation_price = as_float(call_wall) if call_wall is not None else as_float(technical_features.resistance)
    invalidation_rule = "close_above_call_wall" if call_wall is not None else "close_above_resistance"

    return _finalize_signal(
        setup_class="pullback_into_support",
        bias="bearish",
        preconditions_met=preconditions_met,
        components=components,
        reasons=reasons,
        invalidation_price=invalidation_price,
        invalidation_rule=invalidation_rule,
        warnings=warnings,
    )


def evaluate_momentum_continuation_bullish(dealer_features: dict[str, Any], technical_state: TechnicalState) -> SetupSignal:
    components = _base_components()
    reasons: list[str] = []
    warnings: list[str] = []

    spot = as_float(dealer_features.get("spot"))
    call_wall = dealer_features.get("call_wall")
    regime = str(dealer_features.get("regime") or "")

    # Trend-follow longs need price above intraday fair value proxy.
    above_vwap = technical_state.above_vwap
    # EMA stack confirms directional alignment.
    bullish_stack = technical_state.ema_stack == "bullish_stack"
    # High RVOL confirms real participation.
    high_rvol = technical_state.high_rvol
    # Avoid entries directly into likely dealer cap level.
    not_at_call_cap = _pct_diff(as_float(call_wall) if call_wall is not None else None, spot) > MOMENTUM_WALL_AVOID_PCT
    # Prefer dealer regimes with directional potential rather than pinned range.
    regime_ok = regime in {"trend", "acceleration"}

    preconditions_met = above_vwap and bullish_stack and high_rvol and not_at_call_cap and regime_ok

    if not_at_call_cap and regime_ok:
        components["dealer_alignment"] = WEIGHTS["dealer_alignment"]
    if above_vwap and bullish_stack:
        components["technical_alignment"] = WEIGHTS["technical_alignment"]
    if high_rvol:
        components["rvol_confirmation"] = WEIGHTS["rvol_confirmation"]
    else:
        warnings.append("low_rvol")

    reasons.append("bullish momentum continuation conditions evaluated")

    return _finalize_signal(
        setup_class="momentum_continuation",
        bias="bullish",
        preconditions_met=preconditions_met,
        components=components,
        reasons=reasons,
        invalidation_price=None,
        invalidation_rule="close_below_vwap",
        warnings=warnings,
    )


def evaluate_momentum_continuation_bearish(dealer_features: dict[str, Any], technical_state: TechnicalState) -> SetupSignal:
    components = _base_components()
    reasons: list[str] = []
    warnings: list[str] = []

    spot = as_float(dealer_features.get("spot"))
    put_wall = dealer_features.get("put_wall")
    regime = str(dealer_features.get("regime") or "")

    # Bearish continuation expects price under VWAP (distribution).
    below_vwap = not technical_state.above_vwap
    # EMA stack alignment confirms downside structure.
    bearish_stack = technical_state.ema_stack == "bearish_stack"
    # High RVOL confirms active selling pressure.
    high_rvol = technical_state.high_rvol
    # Avoid entering directly into downside support cap.
    not_at_put_floor = _pct_diff(as_float(put_wall) if put_wall is not None else None, spot) > MOMENTUM_WALL_AVOID_PCT
    # Favor directional dealer regime.
    regime_ok = regime in {"trend", "acceleration"}

    preconditions_met = below_vwap and bearish_stack and high_rvol and not_at_put_floor and regime_ok

    if not_at_put_floor and regime_ok:
        components["dealer_alignment"] = WEIGHTS["dealer_alignment"]
    if below_vwap and bearish_stack:
        components["technical_alignment"] = WEIGHTS["technical_alignment"]
    if high_rvol:
        components["rvol_confirmation"] = WEIGHTS["rvol_confirmation"]
    else:
        warnings.append("low_rvol")

    reasons.append("bearish momentum continuation conditions evaluated")

    return _finalize_signal(
        setup_class="momentum_continuation",
        bias="bearish",
        preconditions_met=preconditions_met,
        components=components,
        reasons=reasons,
        invalidation_price=None,
        invalidation_rule="close_above_vwap",
        warnings=warnings,
    )


def evaluate_squeeze_unwind_bullish(
    dealer_features: dict[str, Any], technical_features: TechnicalFeatures, technical_state: TechnicalState
) -> SetupSignal:
    components = _base_components()
    reasons: list[str] = []
    warnings: list[str] = []

    spot = as_float(dealer_features.get("spot"))
    flip_zone = dealer_features.get("flip_zone")

    # Compression indicates spring-loading for range expansion.
    compressed = technical_state.compressed
    # Proximity to flip zone implies unstable dealer positioning / potential acceleration.
    near_flip_zone = _pct_diff(as_float(flip_zone) if flip_zone is not None else None, spot) <= FLIP_ZONE_PROXIMITY_PCT
    # Avoid bullish squeeze if structure is explicitly bearish.
    not_bearish_stack = technical_state.ema_stack != "bearish_stack"

    preconditions_met = compressed and near_flip_zone and not_bearish_stack

    if near_flip_zone:
        components["dealer_alignment"] = WEIGHTS["dealer_alignment"]
    if not_bearish_stack:
        components["technical_alignment"] = WEIGHTS["technical_alignment"]
    if compressed:
        components["compression_bonus"] = WEIGHTS["compression_bonus"]
    if technical_state.high_rvol:
        components["rvol_confirmation"] = WEIGHTS["rvol_confirmation"]
    else:
        warnings.append("low_rvol")
    if flip_zone is None:
        warnings.append("flip_zone_missing")

    reasons.append("bullish squeeze unwind conditions evaluated")

    return _finalize_signal(
        setup_class="squeeze_unwind",
        bias="bullish",
        preconditions_met=preconditions_met,
        components=components,
        reasons=reasons,
        invalidation_price=technical_features.support,
        invalidation_rule="close_below_support_post_squeeze",
        warnings=warnings,
    )


def evaluate_squeeze_unwind_bearish(
    dealer_features: dict[str, Any], technical_features: TechnicalFeatures, technical_state: TechnicalState
) -> SetupSignal:
    components = _base_components()
    reasons: list[str] = []
    warnings: list[str] = []

    spot = as_float(dealer_features.get("spot"))
    flip_zone = dealer_features.get("flip_zone")

    # Compression indicates spring-loading for range expansion.
    compressed = technical_state.compressed
    # Flip-zone proximity identifies unstable dealer region.
    near_flip_zone = _pct_diff(as_float(flip_zone) if flip_zone is not None else None, spot) <= FLIP_ZONE_PROXIMITY_PCT
    # Avoid bearish squeeze if structure is explicitly bullish.
    not_bullish_stack = technical_state.ema_stack != "bullish_stack"

    preconditions_met = compressed and near_flip_zone and not_bullish_stack

    if near_flip_zone:
        components["dealer_alignment"] = WEIGHTS["dealer_alignment"]
    if not_bullish_stack:
        components["technical_alignment"] = WEIGHTS["technical_alignment"]
    if compressed:
        components["compression_bonus"] = WEIGHTS["compression_bonus"]
    if technical_state.high_rvol:
        components["rvol_confirmation"] = WEIGHTS["rvol_confirmation"]
    else:
        warnings.append("low_rvol")
    if flip_zone is None:
        warnings.append("flip_zone_missing")

    reasons.append("bearish squeeze unwind conditions evaluated")

    return _finalize_signal(
        setup_class="squeeze_unwind",
        bias="bearish",
        preconditions_met=preconditions_met,
        components=components,
        reasons=reasons,
        invalidation_price=technical_features.resistance,
        invalidation_rule="close_above_resistance_post_squeeze",
        warnings=warnings,
    )


def evaluate_event_rerating_bullish(
    dealer_features: dict[str, Any], technical_features: TechnicalFeatures, technical_state: TechnicalState, narrative_tags: list[dict[str, Any]]
) -> SetupSignal:
    components = _base_components()
    reasons: list[str] = []
    warnings: list[str] = []

    spot = as_float(dealer_features.get("spot"))
    king = as_float(dealer_features.get("king_node"))
    tag_keys = {str(t.get("tag_key", "")) for t in narrative_tags}

    # Positive catalyst tags are required for rerating thesis.
    narrative_ok = bool(tag_keys & BULLISH_EVENT_TAGS)
    # Spot above king node suggests structure not suppressing upside.
    above_king = spot > king
    # Reject if trend is strongly bearish.
    trend_ok = technical_state.trend in {"uptrend", "strong_uptrend", "neutral"}

    preconditions_met = narrative_ok and above_king and trend_ok

    if above_king:
        components["dealer_alignment"] = WEIGHTS["dealer_alignment"]
    if trend_ok:
        components["technical_alignment"] = WEIGHTS["technical_alignment"]
    if narrative_ok:
        components["narrative_alignment"] = WEIGHTS["narrative_alignment"]
    if technical_state.high_rvol:
        components["rvol_confirmation"] = WEIGHTS["rvol_confirmation"]
    else:
        warnings.append("low_rvol")

    reasons.append("bullish event rerating conditions evaluated")

    return _finalize_signal(
        setup_class="event_rerating",
        bias="bullish",
        preconditions_met=preconditions_met,
        components=components,
        reasons=reasons,
        invalidation_price=king,
        invalidation_rule="close_below_king_node",
        warnings=warnings,
    )


def evaluate_event_rerating_bearish(
    dealer_features: dict[str, Any], technical_features: TechnicalFeatures, technical_state: TechnicalState, narrative_tags: list[dict[str, Any]]
) -> SetupSignal:
    components = _base_components()
    reasons: list[str] = []
    warnings: list[str] = []

    spot = as_float(dealer_features.get("spot"))
    king = as_float(dealer_features.get("king_node"))
    tag_keys = {str(t.get("tag_key", "")) for t in narrative_tags}

    # Negative catalyst tags are required for bearish rerating thesis.
    narrative_ok = bool(tag_keys & BEARISH_EVENT_TAGS)
    # Spot below king node suggests structural overhead pressure.
    below_king = spot < king
    # Reject if trend is strongly bullish.
    trend_ok = technical_state.trend in {"downtrend", "strong_downtrend", "neutral"}

    preconditions_met = narrative_ok and below_king and trend_ok

    if below_king:
        components["dealer_alignment"] = WEIGHTS["dealer_alignment"]
    if trend_ok:
        components["technical_alignment"] = WEIGHTS["technical_alignment"]
    if narrative_ok:
        components["narrative_alignment"] = WEIGHTS["narrative_alignment"]
    if technical_state.high_rvol:
        components["rvol_confirmation"] = WEIGHTS["rvol_confirmation"]
    else:
        warnings.append("low_rvol")

    reasons.append("bearish event rerating conditions evaluated")

    return _finalize_signal(
        setup_class="event_rerating",
        bias="bearish",
        preconditions_met=preconditions_met,
        components=components,
        reasons=reasons,
        invalidation_price=king,
        invalidation_rule="close_above_king_node",
        warnings=warnings,
    )


def evaluate_symbol_snapshot(
    symbol: str,
    dealer_features: dict[str, Any],
    technical_features: TechnicalFeatures,
    technical_state: TechnicalState,
    narrative_tags: list[dict[str, Any]],
    min_confidence: float = 0.3,
) -> list[SetupSignal]:
    """Evaluate all setup variants and return passing signals sorted by confidence."""
    evaluators = [
        lambda: evaluate_pullback_into_support_bullish(dealer_features, technical_features, technical_state),
        lambda: evaluate_pullback_into_support_bearish(dealer_features, technical_features, technical_state),
        lambda: evaluate_momentum_continuation_bullish(dealer_features, technical_state),
        lambda: evaluate_momentum_continuation_bearish(dealer_features, technical_state),
        lambda: evaluate_squeeze_unwind_bullish(dealer_features, technical_features, technical_state),
        lambda: evaluate_squeeze_unwind_bearish(dealer_features, technical_features, technical_state),
        lambda: evaluate_event_rerating_bullish(dealer_features, technical_features, technical_state, narrative_tags),
        lambda: evaluate_event_rerating_bearish(dealer_features, technical_features, technical_state, narrative_tags),
    ]

    signals: list[SetupSignal] = []
    for evaluate in evaluators:
        try:
            signal = evaluate()
            if signal.preconditions_met and signal.confidence_raw >= min_confidence:
                signals.append(signal)
        except Exception as exc:  # pragma: no cover
            logger.warning("setup evaluation failed for %s: %s", symbol, exc)

    signals.sort(key=lambda s: s.confidence_raw, reverse=True)
    return signals
