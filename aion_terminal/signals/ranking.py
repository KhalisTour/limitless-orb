"""Deterministic ranking engine for setup candidates."""
from __future__ import annotations

import json
from typing import Any

from aion_terminal.models.dto import SetupCandidateRecord
from aion_terminal.utils.math_utils import as_float


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _bucket(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _get_snapshot(feature_snapshots: dict[str, Any], candidate: SetupCandidateRecord) -> dict[str, Any]:
    if candidate.candidate_id in feature_snapshots:
        return dict(feature_snapshots[candidate.candidate_id] or {})
    if candidate.symbol in feature_snapshots:
        return dict(feature_snapshots[candidate.symbol] or {})
    return {}


def _decode_rationale(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def rank_candidates(
    candidates: list[SetupCandidateRecord],
    feature_snapshots: dict,
    contract_scores: dict,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []

    for candidate in candidates:
        snapshot = _get_snapshot(feature_snapshots, candidate)
        rationale = _decode_rationale(candidate.rationale_json)
        contract = dict(contract_scores.get(candidate.candidate_id) or contract_scores.get(candidate.symbol) or {})
        direction = candidate.direction or "neutral"
        warnings: list[str] = []

        setup_score_weight = _clamp01(as_float(rationale.get("confidence_raw"), default=as_float(candidate.confidence)))

        ema_stack = str(snapshot.get("ema_stack", rationale.get("ema_stack", "mixed")))
        trend = str(snapshot.get("trend", rationale.get("trend", "neutral")))
        rvol = as_float(snapshot.get("rvol", rationale.get("rvol", 1.0)), default=1.0)
        directional_stack = (direction == "bullish" and ema_stack == "bullish_stack") or (
            direction == "bearish" and ema_stack == "bearish_stack"
        )
        ema_score = 1.0 if directional_stack else (0.4 if ema_stack == "mixed" else 0.1)
        trend_score = 1.0 if trend.startswith("strong_") else (0.8 if trend in {"uptrend", "downtrend"} else 0.3)
        rvol_score = _clamp01((rvol - 0.8) / 1.4)
        technical_alignment_weight = _clamp01((ema_score * 0.45) + (trend_score * 0.35) + (rvol_score * 0.20))

        dist_call = abs(as_float(snapshot.get("distance_to_call_wall"), default=50.0))
        dist_put = abs(as_float(snapshot.get("distance_to_put_wall"), default=50.0))
        wall_distance_score = _clamp01(1.0 - (min(dist_call, dist_put) / 10.0))
        pin_risk = _clamp01(as_float(snapshot.get("pin_risk"), default=0.0))
        accel = _clamp01(as_float(snapshot.get("acceleration_score"), default=0.5))
        dealer_bias = str(snapshot.get("dealer_bias", direction))
        bias_alignment = 1.0 if dealer_bias == direction else 0.2
        dealer_alignment_weight = _clamp01(
            (wall_distance_score * 0.35)
            + ((1.0 - pin_risk) * 0.30)
            + (accel * 0.20)
            + (bias_alignment * 0.15)
        )

        compressed = bool(snapshot.get("compressed", False))
        continuation = bool(snapshot.get("continuation", False))
        vol_regime = str(snapshot.get("vol_regime", "neutral"))
        if compressed:
            volatility_alignment_weight = 0.85
        elif continuation and vol_regime == "high":
            volatility_alignment_weight = 0.75
        elif continuation and vol_regime == "low":
            volatility_alignment_weight = 0.35
            warnings.append("low_vol_breakout_penalty")
        else:
            volatility_alignment_weight = 0.55

        delta_eff = _clamp01(as_float(contract.get("delta_efficiency", contract.get("delta_per_dollar")), default=0.0))
        gamma_eff = _clamp01(as_float(contract.get("gamma_efficiency", contract.get("gamma_per_dollar")), default=0.0))
        theta_burden = _clamp01(as_float(contract.get("theta_burden"), default=1.0))
        liquidity = _clamp01(as_float(contract.get("liquidity_score"), default=0.0))
        contract_quality_weight = _clamp01(
            (delta_eff * 0.30) + (gamma_eff * 0.25) + ((1.0 - theta_burden) * 0.20) + (liquidity * 0.25)
        )

        if liquidity < 0.3:
            warnings.append("low_liquidity")
        if bias_alignment < 1.0:
            warnings.append("bias_conflict")

        ranking_score = _clamp01(
            (setup_score_weight * 0.24)
            + (technical_alignment_weight * 0.22)
            + (dealer_alignment_weight * 0.18)
            + (volatility_alignment_weight * 0.14)
            + (contract_quality_weight * 0.22)
        )

        ranked.append(
            {
                "symbol": candidate.symbol,
                "setup_class": candidate.setup_class,
                "bias": direction,
                "ranking_score": ranking_score,
                "confidence_bucket": _bucket(ranking_score),
                "top_contract": str(contract.get("contract_symbol") or contract.get("option_symbol") or ""),
                "warnings": warnings,
            }
        )

    ranked.sort(key=lambda item: item["ranking_score"], reverse=True)
    return ranked
