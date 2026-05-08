from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from typing import Any

from aion_terminal.arbitration.adaptive import (
    _dte_bucket,
    compute_expectancy_modifier,
    compute_memory_penalty,
    compute_sizing_modifier,
    determine_contract_role,
)
from aion_terminal.arbitration.schemas import (
    AgreementMatrix,
    ArbResult,
    HoldPolicy,
    TriggerSpec,
)
from aion_terminal.arbitration.scoring import (
    _coerce_features,
    _coerce_float,
    _norm_bias,
    detect_conflicts,
    score_contract_agreement,
    score_dealer_agreement,
    score_expectancy_agreement,
    score_macro_agreement,
    score_memory_agreement,
    score_technical_agreement,
)
from aion_terminal.utils.time_utils import utc_now_iso


def _confidence_bucket(c: float) -> str:
    if c >= 0.70:
        return "high"
    if c >= 0.50:
        return "medium"
    if c >= 0.35:
        return "low"
    return "very_low"


def _derive_bias(ranking: dict | None, setup_candidates: list[dict] | None) -> str:
    if setup_candidates:
        for c in setup_candidates:
            d = c.get("direction") or c.get("bias")
            if d:
                return _norm_bias(d)
    if ranking:
        b = ranking.get("bias") or (ranking.get("top_ranked_setup") or {}).get("bias")
        if b:
            return _norm_bias(b)
    return "neutral"


def _derive_setup_class(setup_candidates: list[dict] | None) -> str:
    if setup_candidates:
        for c in setup_candidates:
            sc = c.get("setup_class")
            if sc:
                return str(sc)
    return "technical_dealer_watch"


def _derive_regime(features: dict | None, ranking: dict | None) -> str:
    if features:
        f = _coerce_features(features)
        r = f.get("regime")
        if r:
            return str(r).lower()
    if ranking and ranking.get("regime"):
        return str(ranking.get("regime")).lower()
    return "unknown"


def _derive_moneyness(contracts: dict | None) -> str:
    if not contracts:
        return "unknown"
    best = contracts.get("best") or contracts.get("convex") or contracts.get("safer") or {}
    return str(best.get("moneyness_bucket") or best.get("moneyness") or "unknown")


def _derive_dte_bucket_from_contracts(contracts: dict | None) -> str:
    if not contracts:
        return "unknown"
    best = contracts.get("best") or contracts.get("convex") or contracts.get("safer") or {}
    return _dte_bucket(best.get("dte"))


def _has_acceptance(ranking: dict | None, bias: str) -> bool:
    if not ranking:
        return False
    spot = _coerce_float(ranking.get("spot"), 0.0)
    call_wall = ranking.get("call_wall")
    put_wall = ranking.get("put_wall")
    if not spot:
        return False
    bias_n = _norm_bias(bias)
    try:
        if bias_n == "bullish" and call_wall is not None:
            return float(spot) >= float(call_wall)
        if bias_n == "bearish" and put_wall is not None:
            return float(spot) <= float(put_wall)
    except (TypeError, ValueError):
        return False
    return False


def _build_required_trigger(
    ranking: dict | None,
    bias: str,
    setup_class: str,
    has_acceptance: bool,
    features: dict | None,
) -> TriggerSpec | None:
    bias_n = _norm_bias(bias)
    if not has_acceptance and ranking and bias_n == "bullish":
        level = ranking.get("call_wall") or ranking.get("king_node")
        if level is not None:
            return TriggerSpec(type="acceptance_above", level=float(level), condition="")
    if not has_acceptance and ranking and bias_n == "bearish":
        level = ranking.get("put_wall") or ranking.get("king_node")
        if level is not None:
            return TriggerSpec(type="rejection_below", level=float(level), condition="")
    f = _coerce_features(features or {})
    rvol = _coerce_float(f.get("rvol"), 1.0)
    if "momentum" in str(setup_class).lower() and rvol < 0.5:
        return TriggerSpec(type="volume_confirm", level=None, condition="RVOL > 0.5x avg")
    return None


def _build_kill_switch(ranking: dict | None, bias: str) -> TriggerSpec | None:
    if not ranking:
        return None
    bias_n = _norm_bias(bias)
    if bias_n == "bullish" and ranking.get("put_wall") is not None:
        return TriggerSpec(type="rejection_below", level=float(ranking["put_wall"]), condition="")
    if bias_n == "bearish" and ranking.get("call_wall") is not None:
        return TriggerSpec(type="rejection_above", level=float(ranking["call_wall"]), condition="")
    return None


def run_arbitration(
    symbol: str,
    ranking: dict | None,
    setup_candidates: list[dict] | None,
    features: dict | None,
    contracts: dict | None,
    macro_brief: dict | None,
    memory_summary: dict | None,
    expectancy_data: dict | None,
    rs_candidate: dict | None,
    narrative_tags: list[dict] | None,
    conn: sqlite3.Connection,
) -> ArbResult:
    bias = _derive_bias(ranking, setup_candidates)
    setup_class = _derive_setup_class(setup_candidates)
    regime = _derive_regime(features, ranking)
    has_acceptance = _has_acceptance(ranking, bias)
    moneyness = _derive_moneyness(contracts)
    dte_bucket = _derive_dte_bucket_from_contracts(contracts)

    technical = score_technical_agreement(features, bias)
    dealer = score_dealer_agreement(ranking, bias)
    contracts_score = score_contract_agreement(contracts, bias)
    macro = score_macro_agreement(macro_brief, bias)
    memory = score_memory_agreement(memory_summary, bias, setup_class)

    if expectancy_data is None:
        try:
            mod = compute_expectancy_modifier(
                conn, setup_class, regime, bias, dte_bucket, moneyness
            )
            expectancy_data = {"expectancy_modifier": mod}
        except Exception:
            expectancy_data = None
    expectancy = score_expectancy_agreement(expectancy_data, setup_class, regime)

    agreement = AgreementMatrix(
        technical=technical,
        dealer=dealer,
        contracts=contracts_score,
        macro=macro,
        memory=memory,
        expectancy=expectancy,
    )

    conflicts = detect_conflicts(ranking, features, macro_brief, contracts, bias)

    try:
        memory_penalty_data = compute_memory_penalty(conn, symbol, setup_class)
    except Exception:
        memory_penalty_data = {"total_penalty": 0.0, "reasons": []}

    expectancy_modifier = (expectancy_data or {}).get("expectancy_modifier", 0.0) or 0.0
    contract_role = determine_contract_role(
        agreement, conflicts, expectancy_modifier, memory_penalty_data["total_penalty"]
    )

    avg = agreement.average()
    if bias == "neutral" and not setup_candidates:
        confidence = avg * 0.5
    else:
        confidence = avg
    confidence = max(0.0, min(1.0, confidence))

    sizing = compute_sizing_modifier(
        confidence, avg, memory_penalty_data["total_penalty"], regime, has_acceptance
    )

    required_trigger = _build_required_trigger(ranking, bias, setup_class, has_acceptance, features)
    kill_switch = _build_kill_switch(ranking, bias)

    if avg >= 0.70 and not any(c in {"no_liquid_contracts", "macro_risk_not_supportive"} for c in conflicts):
        arb_decision = "trade"
    elif avg >= 0.50 and required_trigger is not None:
        arb_decision = "wait_for_trigger"
    elif avg >= 0.35:
        arb_decision = "reduce_only"
    else:
        arb_decision = "no_trade"

    if contract_role == "no_trade":
        arb_decision = "no_trade"

    supporting: list[str] = []
    rejection: list[str] = []
    if technical >= 0.65:
        supporting.append("bullish_ema_stack" if bias == "bullish" else "aligned_technical_stack")
    if dealer >= 0.65:
        supporting.append(f"{regime}_regime" if regime != "unknown" else "supportive_dealer_structure")
    if macro >= 0.65:
        supporting.append("macro_supportive")
    if memory >= 0.65:
        supporting.append("memory_supportive")
    if expectancy >= 0.65:
        supporting.append("positive_historical_expectancy")
    rejection.extend(conflicts)

    warnings = list(memory_penalty_data.get("reasons") or [])
    if not has_acceptance and bias != "neutral":
        warnings.append("acceptance_pending")

    inputs_summary = {
        "bias": bias,
        "setup_class": setup_class,
        "regime": regime,
        "moneyness": moneyness,
        "dte_bucket": dte_bucket,
        "has_acceptance": has_acceptance,
        "expectancy_modifier": expectancy_modifier,
        "memory_penalty": memory_penalty_data["total_penalty"],
    }

    result = ArbResult(
        symbol=symbol.upper(),
        generated_at=utc_now_iso(),
        arb_decision=arb_decision,
        final_bias=bias,
        confidence=confidence,
        confidence_bucket=_confidence_bucket(confidence),
        setup_class=setup_class,
        agreement_matrix=agreement,
        conflicts=conflicts,
        required_trigger=required_trigger,
        kill_switch=kill_switch,
        approved_contract_role=contract_role,
        sizing_modifier=sizing,
        hold_policy=HoldPolicy(),
        warnings=warnings,
        supporting_factors=supporting,
        rejection_factors=rejection,
        inputs_summary=inputs_summary,
    )

    try:
        _persist_arbitration(conn, result)
    except Exception:
        pass

    return result


def _persist_arbitration(conn: sqlite3.Connection, result: ArbResult) -> None:
    arb_id = str(uuid.uuid4())
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO arbitration_snapshots (
            arb_id, generated_at, symbol, arb_decision, final_bias, confidence,
            confidence_bucket, setup_class, agreement_json, conflicts_json,
            required_trigger_json, kill_switch_json, approved_contract_role,
            sizing_modifier, hold_policy_json, warnings_json, supporting_factors_json,
            rejection_factors_json, inputs_summary_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            arb_id,
            result.generated_at,
            result.symbol,
            result.arb_decision,
            result.final_bias,
            result.confidence,
            result.confidence_bucket,
            result.setup_class,
            json.dumps(asdict(result.agreement_matrix)),
            json.dumps(result.conflicts),
            json.dumps(asdict(result.required_trigger)) if result.required_trigger else None,
            json.dumps(asdict(result.kill_switch)) if result.kill_switch else None,
            result.approved_contract_role,
            result.sizing_modifier,
            json.dumps(asdict(result.hold_policy)),
            json.dumps(result.warnings),
            json.dumps(result.supporting_factors),
            json.dumps(result.rejection_factors),
            json.dumps(result.inputs_summary, default=str),
            now,
        ),
    )
    conn.commit()
