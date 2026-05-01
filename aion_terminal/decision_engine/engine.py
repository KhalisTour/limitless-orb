from __future__ import annotations

from typing import Any


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _f(value)
        if parsed is not None:
            return parsed
    return None


def compute_decision_engine_state(
    spot: float,
    bars: dict | None = None,
    chain: dict | None = None,
    position: dict | None = None,
    technical_state: dict | None = None,
    dealer_structure: dict | None = None,
    user_constraints: dict | None = None,
) -> dict:
    bars = bars or {}
    chain = chain or {}
    position = position or {}
    technical_state = technical_state or {}
    dealer_structure = dealer_structure or {}
    user_constraints = user_constraints or {}

    warnings: list[str] = []
    derived_from: list[str] = []

    s1_candidates = {
        "recent_swing_high": _f(bars.get("recent_swing_high")),
        "premarket_high": _f(bars.get("premarket_high")),
        "orb_high": _f(bars.get("orb_high")),
        "vwap_reclaim": _f(technical_state.get("vwap_reclaim_level")),
        "call_wall": _f(dealer_structure.get("call_wall")),
        "upper_expected_move": _f(chain.get("upper_expected_move")),
        "prior_rejection": _f(bars.get("prior_rejection_level")),
    }
    s3_candidates = {
        "recent_swing_low": _f(bars.get("recent_swing_low")),
        "vwap_support": _f(technical_state.get("vwap_support")),
        "orb_low": _f(bars.get("orb_low")),
        "prior_support": _f(bars.get("prior_support")),
        "put_wall": _f(dealer_structure.get("put_wall")),
        "lower_expected_move": _f(chain.get("lower_expected_move")),
        "failed_breakout_retest": _f(bars.get("failed_breakout_retest")),
    }

    valid_s1 = [v for v in s1_candidates.values() if v is not None]
    valid_s3 = [v for v in s3_candidates.values() if v is not None]

    s1_level = max(valid_s1) if valid_s1 else None
    s3_level = min(valid_s3) if valid_s3 else None

    for name, value in s1_candidates.items():
        if value is not None:
            derived_from.append(f"s1:{name}")
    for name, value in s3_candidates.items():
        if value is not None:
            derived_from.append(f"s3:{name}")

    if s1_level is None:
        warnings.append("missing_s1_inputs")
    if s3_level is None:
        warnings.append("missing_s3_inputs")

    rvol = _first_float(technical_state.get("rvol"), technical_state.get("relative_volume"), 1.0) or 1.0
    close_above_s1 = bool(technical_state.get("close_above_s1") or technical_state.get("close_above_breakout"))
    retest_hold = bool(technical_state.get("retest_hold") or technical_state.get("breakout_retest_hold"))
    higher_low_above_s1 = bool(technical_state.get("higher_low_above_s1"))
    above_vwap = bool(technical_state.get("above_vwap", True))
    above_ema = bool(technical_state.get("above_ema", True))
    rejection_wick = bool(technical_state.get("rejection_wick"))
    failed_breakout = bool(technical_state.get("failed_breakout"))

    acceptance_score = 0.0
    acceptance_score += 0.35 if close_above_s1 else 0.0
    acceptance_score += 0.25 if retest_hold else 0.0
    acceptance_score += 0.2 if higher_low_above_s1 else 0.0
    acceptance_score += 0.2 if rvol >= 1.5 else (-0.15 if rvol < 0.7 else 0.0)
    acceptance_score += 0.1 if above_vwap else -0.1
    acceptance_score += 0.1 if above_ema else -0.05
    acceptance_score -= 0.35 if rejection_wick else 0.0
    acceptance_score -= 0.35 if failed_breakout else 0.0
    acceptance_score = _clamp(acceptance_score, -1.0, 1.0)

    invalidation_evidence = bool(technical_state.get("invalidation_triggered") or technical_state.get("lost_support"))
    state = "S2"
    if s1_level is not None and spot >= s1_level and acceptance_score > 0.2:
        state = "S1"
    elif (s3_level is not None and spot <= s3_level) or invalidation_evidence:
        state = "S3"

    atr = _first_float(technical_state.get("atr"), bars.get("atr"), max(abs(spot) * 0.02, 0.01)) or 1.0
    iv = _first_float(position.get("iv"), chain.get("iv"), 0.35) or 0.35
    dte = int(_first_float(position.get("dte"), chain.get("dte"), 5) or 5)
    dte = max(0, dte)
    horizon_scale = _clamp(dte / 5.0 if dte else 0.6, 0.6, 4.0)

    def touch_probability(level: float | None) -> float:
        if level is None:
            warnings.append("missing_level_for_touch_probability")
            return 0.45
        distance = abs(level - spot)
        base_vol = max(atr, abs(spot) * iv * 0.02)
        normalized_distance = distance / max(base_vol * horizon_scale, 1e-6)
        return _clamp(1.0 - normalized_distance, 0.05, 0.95)

    p_touch_s1 = touch_probability(s1_level)
    p_touch_s3 = touch_probability(s3_level)

    moneyness = str(position.get("moneyness") or chain.get("moneyness") or "ATM").upper()
    delta = _first_float(position.get("delta"), chain.get("delta"))
    gamma = _first_float(position.get("gamma"), chain.get("gamma"), 0.03) or 0.03
    theta_daily_pct = _first_float(position.get("theta_daily_pct"), chain.get("theta_daily_pct"), 0.08) or 0.08

    call_wall = _f(dealer_structure.get("call_wall"))
    put_wall = _f(dealer_structure.get("put_wall"))
    king_node = _f(dealer_structure.get("king_node"))

    state_score = {"S1": 2.0, "S2": 0.0, "S3": -3.0}[state]
    profit_probability_score = (p_touch_s1 - p_touch_s3) * 1.7
    touch_probability_score = (0.5 - p_touch_s3) + (p_touch_s1 - 0.5)

    option_structure_score = 0.0
    if delta is not None and delta < 0.30 and state == "S2":
        option_structure_score -= 0.8
    if delta is not None and 0.40 <= delta <= 0.60 and state == "S1":
        option_structure_score += 0.8
    if delta is not None and delta > 0.65 and moneyness == "ITM":
        option_structure_score += 0.6
    if gamma > 0.08 and state == "S1":
        option_structure_score += 0.5
    if gamma > 0.08 and state == "S2":
        option_structure_score -= 0.5

    oi_structure_score = 0.0
    if call_wall is not None and spot < call_wall:
        oi_structure_score -= 0.25
    if call_wall is not None and spot > call_wall and state == "S1":
        oi_structure_score += 0.45
    if put_wall is not None and spot > put_wall:
        oi_structure_score += 0.2
    if put_wall is not None and spot < put_wall:
        oi_structure_score -= 0.65

    pin_risk_value = 0.0
    if king_node is not None and abs(spot - king_node) / max(abs(spot), 1.0) < 0.003 and dte <= 2:
        pin_risk_value = 0.7
        warnings.append("pin_zone_risk")

    theta_risk_value = 0.9 if theta_daily_pct > 0.15 and state != "S1" else (0.4 if theta_daily_pct > 0.1 else 0.1)

    session_move_pct = abs(_first_float(technical_state.get("session_move_pct"), 0.0) or 0.0)
    iv_crush_risk_value = 0.6 if iv > 0.7 and session_move_pct > 2.5 else 0.1

    position_size_pct = _first_float(user_constraints.get("position_size_pct"), position.get("position_size_pct"), 0.0) or 0.0
    sizing_risk_value = 0.5 if position_size_pct > 0.04 else 0.0

    ev_score = (
        state_score
        + acceptance_score
        + profit_probability_score
        + touch_probability_score
        + option_structure_score
        + oi_structure_score
        - theta_risk_value
        - iv_crush_risk_value
        - pin_risk_value
        - sizing_risk_value
    )

    action_bias = "hold"
    if ev_score >= 2.0:
        action_bias = "add" if state == "S1" else "hold"
    elif ev_score >= 0.8:
        action_bias = "hold"
    elif ev_score >= -0.3:
        action_bias = "trim"
    elif ev_score >= -1.5:
        action_bias = "exit" if state == "S3" else "no_trade"
    else:
        action_bias = "exit"

    if moneyness == "OTM" and dte <= 2 and state == "S2":
        action_bias = "trim"
    if moneyness == "OTM" and state == "S3":
        action_bias = "exit"

    return {
        "state": state,
        "s1_level": s1_level,
        "s2_range": [s3_level, s1_level],
        "s3_level": s3_level,
        "acceptance_score": acceptance_score,
        "p_touch_s1": p_touch_s1,
        "p_touch_s3": p_touch_s3,
        "ev_score": ev_score,
        "delta_context": f"delta={delta}" if delta is not None else "delta_unknown",
        "gamma_context": "high_gamma" if gamma > 0.08 else "normal_gamma",
        "theta_risk": "high" if theta_daily_pct > 0.15 else "moderate" if theta_daily_pct > 0.1 else "low",
        "iv_context": "elevated" if iv > 0.7 else "normal",
        "oi_context": "call_wall_overhead" if call_wall is not None and spot < call_wall else "supportive_or_neutral",
        "pin_risk": "high" if pin_risk_value > 0.5 else "low",
        "expected_pnl_profile": "expansion" if p_touch_s1 > p_touch_s3 and state != "S3" else "decay_or_downside",
        "action_bias": action_bias,
        "warnings": warnings,
        "derived_from": derived_from,
    }
