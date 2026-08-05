from __future__ import annotations

import json
from typing import Any

from aion_terminal.models.enums import EMAStack, Regime, TrendState


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _features(features: dict | None) -> dict:
    if not features:
        return {}
    if "features_json" in features and not features.get("ema_stack"):
        return _as_dict(features.get("features_json"))
    return features


def score_technical_agreement(features: dict | None, bias: str) -> float:
    """0..1 alignment of technicals with bias."""
    f = _features(features)
    if not f:
        return 0.5
    bias = (bias or "").lower()
    if bias not in ("bullish", "bearish"):
        return 0.5

    score = 0.5
    ema_stack = (f.get("ema_stack") or "").lower()
    trend = (f.get("trend") or "").lower()
    rvol = f.get("rvol")
    compressed = bool(f.get("compressed"))
    pullback_depth = f.get("pullback_depth")

    # Trend vocabulary comes from TrendState (see models.enums). Matching only
    # the plain up/downtrend literals silently discarded `strong_uptrend` and
    # `strong_downtrend` — the highest-conviction readings the producer emits.
    # `bullish`/`bearish` are accepted as legacy aliases for rows written before
    # the vocabulary was pinned down.
    bullish_trend = TrendState.bullish_values() | {"up", "bullish"}
    bearish_trend = TrendState.bearish_values() | {"down", "bearish"}

    if bias == "bullish":
        if ema_stack == EMAStack.BULLISH.value:
            score += 0.2
        elif ema_stack == EMAStack.BEARISH.value:
            score -= 0.25
        if trend in bullish_trend:
            score += 0.1
        elif trend in bearish_trend:
            score -= 0.15
    else:
        if ema_stack == EMAStack.BEARISH.value:
            score += 0.2
        elif ema_stack == EMAStack.BULLISH.value:
            score -= 0.25
        if trend in bearish_trend:
            score += 0.1
        elif trend in bullish_trend:
            score -= 0.15

    try:
        rvol_v = float(rvol)
        if rvol_v >= 1.0:
            score += 0.1
        elif rvol_v < 0.3:
            score -= 0.1
    except (TypeError, ValueError):
        pass

    if compressed:
        score += 0.05
    try:
        if pullback_depth is not None:
            pd = float(pullback_depth)
            if 0.0 < pd < 0.05:
                score += 0.05
    except (TypeError, ValueError):
        pass

    return _clamp(score)


def _pct_distance(target: float | None, spot: float | None) -> float | None:
    if target is None or spot is None or spot == 0:
        return None
    try:
        return (float(target) - float(spot)) / float(spot)
    except (TypeError, ValueError):
        return None


def score_dealer_agreement(ranking: dict | None, bias: str) -> float:
    if not ranking:
        return 0.5
    bias = (bias or "").lower()
    score = 0.5

    regime = (ranking.get("regime") or "").lower()
    spot = ranking.get("spot")
    dealer = ranking.get("dealer_structure") or {}
    call_wall = dealer.get("call_wall") or ranking.get("call_wall")
    put_wall = dealer.get("put_wall") or ranking.get("put_wall")
    king_node = dealer.get("king_node") or ranking.get("king_node")

    cw_d = _pct_distance(call_wall, spot)
    pw_d = _pct_distance(put_wall, spot)
    kn_d = _pct_distance(king_node, spot)

    # Regime is directionless structure (see features.dealer): a gamma vacuum
    # (acceleration) or an established trend amplifies whichever bias is in play,
    # while a range works against a directional thesis. Scored symmetrically so
    # neither side gets a free long/short bias, and every producible regime value
    # (acceleration/trend/range) is handled — no silent no-op fall-through.
    score += _regime_alignment(regime)

    if bias == "bullish":
        if cw_d is not None and 0 < cw_d < 0.02:
            # Pinned directly under the call wall: little room to run.
            score -= 0.2
        if cw_d is not None and cw_d > 0.05:
            # Headroom overhead toward the target.
            score += 0.05
        if pw_d is not None and pw_d < -0.02:
            # Put-wall support sits comfortably below spot.
            score += 0.05
    elif bias == "bearish":
        if pw_d is not None and -0.02 < pw_d < 0:
            # Pinned directly above the put wall: little room to fall.
            score -= 0.2
        if pw_d is not None and pw_d < -0.05:
            # Room down toward the target below the put wall.
            score += 0.05
        if cw_d is not None and cw_d > 0.02:
            # Call-wall resistance caps the upside, favouring the short.
            score += 0.05

    if kn_d is not None and abs(kn_d) < 0.005:
        score -= 0.05
    return _clamp(score)


def _regime_alignment(regime: str) -> float:
    """Directionless regime contribution, applied identically to both biases."""
    if regime == Regime.ACCELERATION.value:
        return 0.25
    if regime == Regime.TREND.value:
        return 0.1
    if regime == Regime.RANGE.value:
        return -0.05
    return 0.0


def score_contract_agreement(contracts: dict | None, bias: str) -> float:
    if not contracts:
        return 0.3
    best = contracts.get("best") if isinstance(contracts, dict) else None
    if not best:
        return 0.3
    score = 0.5
    total = best.get("total_score") or best.get("score")
    spread_pct = best.get("spread_pct")
    liq = best.get("liquidity_score")
    oi = best.get("open_interest")
    vol = best.get("volume")

    try:
        if total is not None:
            t = float(total)
            if t >= 70:
                score += 0.25
            elif t >= 50:
                score += 0.1
            elif t < 30:
                score -= 0.1
    except (TypeError, ValueError):
        pass
    try:
        if spread_pct is not None:
            sp = float(spread_pct)
            if sp > 0.20:
                score -= 0.25
            elif sp > 0.10:
                score -= 0.1
            elif sp < 0.03:
                score += 0.05
    except (TypeError, ValueError):
        pass
    try:
        if liq is not None:
            lv = float(liq)
            if lv >= 0.7:
                score += 0.05
            elif lv < 0.3:
                score -= 0.05
    except (TypeError, ValueError):
        pass
    try:
        if vol is not None and float(vol) <= 0:
            score -= 0.2
        if oi is not None and float(oi) <= 0:
            score -= 0.1
    except (TypeError, ValueError):
        pass
    return _clamp(score)


def score_macro_agreement(macro_brief: dict | None, bias: str) -> float:
    if not macro_brief:
        return 0.5
    bias = (bias or "").lower()
    regime = (macro_brief.get("regime") or "").lower()
    risk = (macro_brief.get("risk_level") or "").lower()
    score = 0.5
    if bias == "bullish":
        if regime in ("risk_on", "risk-on", "expansion", "recovery"):
            score = 0.8
        elif regime in ("stagflation", "stagflationary", "contraction", "risk_off", "risk-off"):
            score = 0.3
        elif regime in ("neutral", "mixed"):
            score = 0.55
    elif bias == "bearish":
        if regime in ("risk_off", "risk-off", "contraction", "stagflation", "stagflationary"):
            score = 0.75
        elif regime in ("risk_on", "risk-on", "expansion"):
            score = 0.3
    if risk in ("high", "elevated") and bias == "bullish":
        score -= 0.05
    return _clamp(score)


def score_memory_agreement(memory_summary: dict | None, bias: str, setup_class: str) -> float:
    if not memory_summary:
        return 0.5
    summary = _as_dict(memory_summary.get("summary_json") if "summary_json" in memory_summary else memory_summary)
    score = 0.5
    patterns = summary.get("penalty_patterns") or summary.get("patterns") or []
    if isinstance(patterns, dict):
        patterns = list(patterns.keys())
    for p in patterns:
        ps = str(p).lower()
        if "early_exit" in ps or "premature_exit" in ps:
            score -= 0.15
        if "far_otm" in ps or "far otm" in ps:
            score -= 0.20
        if "oversized" in ps or "over_sized" in ps:
            score -= 0.15
    wr = summary.get("win_rate")
    try:
        if wr is not None:
            wrv = float(wr)
            if wrv >= 0.6:
                score += 0.1
            elif wrv < 0.3:
                score -= 0.1
    except (TypeError, ValueError):
        pass
    return _clamp(score)


def score_expectancy_agreement(expectancy: dict | None, setup_class: str, regime: str) -> float:
    if not expectancy:
        return 0.5
    mod = expectancy.get("expectancy_modifier")
    if mod is None:
        return 0.5
    try:
        m = float(mod)
    except (TypeError, ValueError):
        return 0.5
    return _clamp(0.5 + (m / 2.0))


def detect_conflicts(
    ranking: dict | None,
    features: dict | None,
    macro_brief: dict | None,
    contracts: dict | None,
    bias: str,
) -> list[str]:
    conflicts: list[str] = []
    bias = (bias or "").lower()

    spot = (ranking or {}).get("spot")
    dealer = (ranking or {}).get("dealer_structure") or {}
    call_wall = dealer.get("call_wall") or (ranking or {}).get("call_wall")
    put_wall = dealer.get("put_wall") or (ranking or {}).get("put_wall")
    regime = ((ranking or {}).get("regime") or "").lower()

    cw_d = _pct_distance(call_wall, spot)
    pw_d = _pct_distance(put_wall, spot)

    if bias == "bullish" and cw_d is not None and 0 < cw_d < 0.02:
        conflicts.append("near_call_wall_resistance")
    if bias == "bearish" and pw_d is not None and -0.02 < pw_d < 0:
        conflicts.append("near_put_wall_support")

    if macro_brief:
        mregime = (macro_brief.get("regime") or "").lower()
        if bias == "bullish" and mregime in ("stagflation", "stagflationary", "risk_off", "risk-off", "contraction"):
            conflicts.append("macro_risk_not_supportive")
        if bias == "bearish" and mregime in ("risk_on", "risk-on", "expansion"):
            conflicts.append("macro_risk_not_supportive")

    if contracts:
        best = contracts.get("best") if isinstance(contracts, dict) else None
        if not best:
            conflicts.append("no_liquid_contracts")
        else:
            sp = best.get("spread_pct")
            vol = best.get("volume")
            try:
                if sp is not None and float(sp) > 0.20:
                    conflicts.append("no_liquid_contracts")
                elif vol is not None and float(vol) <= 0:
                    conflicts.append("no_liquid_contracts")
            except (TypeError, ValueError):
                pass
    else:
        conflicts.append("no_liquid_contracts")

    f = _features(features)
    rvol = f.get("rvol")
    try:
        if rvol is not None and float(rvol) < 0.3:
            conflicts.append("low_rvol_entry")
    except (TypeError, ValueError):
        pass

    if regime in ("range", "ranging"):
        setup_hint = (f.get("setup_class") or "").lower()
        if "momentum" in setup_hint or "continuation" in setup_hint:
            conflicts.append("regime_mismatch")

    if bias == "bullish" and cw_d is not None and cw_d > 0 and spot and call_wall:
        try:
            if float(spot) < float(call_wall):
                conflicts.append("acceptance_not_confirmed")
        except (TypeError, ValueError):
            pass

    return list(dict.fromkeys(conflicts))
