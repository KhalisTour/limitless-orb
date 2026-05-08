from __future__ import annotations

import json
from typing import Any


def _norm_bias(bias: str | None) -> str:
    if not bias:
        return "neutral"
    b = str(bias).lower()
    if b in {"bull", "bullish", "long", "up"}:
        return "bullish"
    if b in {"bear", "bearish", "short", "down"}:
        return "bearish"
    return "neutral"


def _coerce_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _coerce_features(features: dict | None) -> dict:
    if not features:
        return {}
    if "features_json" in features and isinstance(features.get("features_json"), str):
        try:
            inner = json.loads(features["features_json"])
            merged = dict(features)
            merged.update(inner or {})
            return merged
        except Exception:
            return dict(features)
    return dict(features)


def score_technical_agreement(features: dict | None, bias: str) -> float:
    f = _coerce_features(features)
    if not f:
        return 0.5
    bias_n = _norm_bias(bias)
    score = 0.5

    ema_stack = str(f.get("ema_stack") or "").lower()
    trend = str(f.get("trend") or "").lower()
    rvol = _coerce_float(f.get("rvol"), 0.0)
    compressed = bool(f.get("compressed", False))
    pullback_depth = _coerce_float(f.get("pullback_depth"), 0.0)

    if bias_n == "bullish":
        if "bullish" in ema_stack or "stacked_up" in ema_stack:
            score += 0.2
        elif "bearish" in ema_stack or "stacked_down" in ema_stack:
            score -= 0.25
        if trend in {"up", "uptrend", "rising"}:
            score += 0.1
        elif trend in {"down", "downtrend", "falling"}:
            score -= 0.15
    elif bias_n == "bearish":
        if "bearish" in ema_stack or "stacked_down" in ema_stack:
            score += 0.2
        elif "bullish" in ema_stack or "stacked_up" in ema_stack:
            score -= 0.25
        if trend in {"down", "downtrend", "falling"}:
            score += 0.1
        elif trend in {"up", "uptrend", "rising"}:
            score -= 0.15

    if rvol >= 1.0:
        score += 0.05
    elif rvol > 0 and rvol < 0.3:
        score -= 0.1

    if compressed:
        score += 0.05
    if pullback_depth > 0:
        score += 0.03

    return max(0.0, min(1.0, score))


def score_dealer_agreement(ranking: dict | None, bias: str) -> float:
    if not ranking:
        return 0.5
    bias_n = _norm_bias(bias)
    regime = str(ranking.get("regime") or "").lower()
    score = 0.5

    call_wall_pct = _coerce_float(ranking.get("call_wall_pct"), 0.0)
    put_wall_pct = _coerce_float(ranking.get("put_wall_pct"), 0.0)

    if bias_n == "bullish":
        if regime in {"acceleration", "trending", "trend"}:
            score += 0.25
        elif regime in {"range", "rangebound", "compression"}:
            score -= 0.05
        elif regime in {"distribution", "breakdown"}:
            score -= 0.2
        if 0 < call_wall_pct <= 2.0:
            score -= 0.2
        elif call_wall_pct > 5.0:
            score += 0.1
        if put_wall_pct < 0 and abs(put_wall_pct) >= 3.0:
            score += 0.05
    elif bias_n == "bearish":
        if regime in {"distribution", "breakdown", "downtrend"}:
            score += 0.25
        elif regime in {"acceleration", "trending"}:
            score -= 0.2
        if put_wall_pct < 0 and abs(put_wall_pct) <= 2.0:
            score -= 0.2
        if 0 < call_wall_pct <= 2.0:
            score += 0.05

    return max(0.0, min(1.0, score))


def score_contract_agreement(contracts: dict | None, bias: str) -> float:
    if not contracts:
        return 0.3
    best = contracts.get("best") or contracts.get("convex") or contracts.get("safer")
    if not best:
        return 0.3

    score = 0.5
    total = _coerce_float(best.get("total_score"), 0.0)
    if total > 0:
        score = max(score, min(1.0, total / 100.0))

    spread_pct = _coerce_float(best.get("spread_pct"), 0.0)
    liquidity = _coerce_float(best.get("liquidity_score"), 0.0)
    oi = _coerce_float(best.get("open_interest") or best.get("oi"), 0.0)
    vol = _coerce_float(best.get("volume"), 0.0)

    if spread_pct > 20:
        score -= 0.25
    elif spread_pct > 10:
        score -= 0.1

    if liquidity > 0:
        score += min(0.15, liquidity / 200.0)

    if oi < 100:
        score -= 0.05
    if vol <= 0:
        score -= 0.1

    return max(0.0, min(1.0, score))


def score_macro_agreement(macro_brief: dict | None, bias: str) -> float:
    if not macro_brief:
        return 0.5
    bias_n = _norm_bias(bias)
    regime = str(macro_brief.get("regime") or macro_brief.get("market_regime") or "").lower()

    if bias_n == "bullish":
        if "risk-on" in regime or "risk_on" in regime or regime == "bullish":
            return 0.8
        if "stagflation" in regime:
            return 0.3
        if "risk-off" in regime or "risk_off" in regime or regime == "bearish":
            return 0.35
        return 0.5
    elif bias_n == "bearish":
        if "risk-off" in regime or "risk_off" in regime or regime == "bearish":
            return 0.8
        if "stagflation" in regime:
            return 0.65
        if "risk-on" in regime or "risk_on" in regime or regime == "bullish":
            return 0.3
        return 0.5
    return 0.5


def score_memory_agreement(memory_summary: dict | None, bias: str, setup_class: str) -> float:
    if not memory_summary:
        return 0.5
    payload: dict = {}
    if isinstance(memory_summary, dict):
        if "summary_json" in memory_summary and isinstance(memory_summary["summary_json"], str):
            try:
                payload = json.loads(memory_summary["summary_json"])
            except Exception:
                payload = {}
        else:
            payload = dict(memory_summary)

    score = 0.5
    warnings = payload.get("common_warnings") or []
    behaviors = payload.get("behavioral_notes") or []
    text = " ".join(str(w).lower() for w in [*warnings, *behaviors])

    if "early_exit" in text or "early exits" in text:
        score -= 0.15
    if "far_otm" in text or "far otm" in text:
        score -= 0.20
    if "oversized" in text or "oversize" in text:
        score -= 0.15
    if "favor_plan_adherence" in text or "stable_execution" in text:
        score += 0.05

    win_rate = _coerce_float(payload.get("win_rate"), -1.0)
    if win_rate >= 0:
        score += (win_rate - 0.5) * 0.3

    return max(0.0, min(1.0, score))


def score_expectancy_agreement(expectancy: dict | None, setup_class: str, regime: str) -> float:
    if not expectancy:
        return 0.5
    modifier = _coerce_float(expectancy.get("expectancy_modifier"), 0.0)
    return max(0.0, min(1.0, 0.5 + (modifier * 0.5)))


def detect_conflicts(
    ranking: dict | None,
    features: dict | None,
    macro_brief: dict | None,
    contracts: dict | None,
    bias: str,
) -> list[str]:
    conflicts: list[str] = []
    bias_n = _norm_bias(bias)
    f = _coerce_features(features)

    if ranking:
        call_wall_pct = _coerce_float(ranking.get("call_wall_pct"), 0.0)
        put_wall_pct = _coerce_float(ranking.get("put_wall_pct"), 0.0)
        regime = str(ranking.get("regime") or "").lower()
        spot = _coerce_float(ranking.get("spot"), 0.0)
        call_wall = ranking.get("call_wall")
        if bias_n == "bullish" and 0 < call_wall_pct <= 2.0:
            conflicts.append("near_call_wall_resistance")
        if bias_n == "bearish" and put_wall_pct < 0 and abs(put_wall_pct) <= 2.0:
            conflicts.append("near_put_wall_support")
        if regime in {"range", "rangebound", "compression"}:
            conflicts.append("regime_mismatch")
        if bias_n == "bullish" and call_wall is not None and spot:
            try:
                if float(spot) < float(call_wall):
                    conflicts.append("acceptance_not_confirmed")
            except (TypeError, ValueError):
                pass

    if macro_brief:
        regime = str(macro_brief.get("regime") or macro_brief.get("market_regime") or "").lower()
        if bias_n == "bullish" and ("stagflation" in regime or "risk-off" in regime or "risk_off" in regime):
            conflicts.append("macro_risk_not_supportive")
        if bias_n == "bearish" and ("risk-on" in regime or "risk_on" in regime):
            conflicts.append("macro_risk_not_supportive")

    if contracts:
        best = contracts.get("best") or contracts.get("convex") or contracts.get("safer")
        if not best:
            conflicts.append("no_liquid_contracts")
        else:
            spread_pct = _coerce_float(best.get("spread_pct"), 0.0)
            vol = _coerce_float(best.get("volume"), 0.0)
            if spread_pct > 20 or vol <= 0:
                conflicts.append("no_liquid_contracts")
    else:
        conflicts.append("no_liquid_contracts")

    if f:
        rvol = _coerce_float(f.get("rvol"), 1.0)
        setup_class = str(f.get("setup_class") or "").lower()
        if rvol < 0.3 and ("momentum" in setup_class or bias_n != "neutral"):
            conflicts.append("low_rvol_entry")

    return conflicts
