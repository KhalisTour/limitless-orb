from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Any

from aion_terminal.arbitration import scoring
from aion_terminal.arbitration.adaptive import (
    compute_expectancy_modifier,
    has_expectancy_history,
    compute_memory_penalty,
    compute_sizing_modifier,
    determine_contract_role,
    _bucket_dte,
)
from aion_terminal.arbitration.schemas import (
    AgreementMatrix,
    ArbResult,
    HoldPolicy,
    TriggerSpec,
)
from aion_terminal.models.enums import EMAStack
from aion_terminal.utils.time_utils import utc_now_iso


DEGRADED_SETUP_CLASS = "technical_dealer_watch"
NO_SETUP_CLASS = "none"

logger = logging.getLogger(__name__)

# Incremented whenever an arbitration row fails to persist. Read by diagnostics:
# a non-zero value means decisions were produced without a reasoning record.
_PERSIST_FAILURES = 0


def persistence_failure_count() -> int:
    """Number of arbitration rows that failed to persist this process."""
    return _PERSIST_FAILURES


def reset_persistence_failure_count() -> None:
    global _PERSIST_FAILURES
    _PERSIST_FAILURES = 0


def _confidence_bucket(c: float) -> str:
    if c >= 0.75:
        return "high"
    if c >= 0.55:
        return "medium"
    if c >= 0.35:
        return "low"
    return "very_low"


def _derive_bias(ranking: dict | None, setup_candidates: list[dict] | None) -> str:
    if setup_candidates:
        first = setup_candidates[0]
        direction = (first.get("direction") or "").lower()
        if direction in ("long", "bullish", "up"):
            return "bullish"
        if direction in ("short", "bearish", "down"):
            return "bearish"
    if ranking:
        top = ranking.get("top_ranked_setup") or {}
        b = (top.get("bias") or ranking.get("bias") or "").lower()
        if b in ("bullish", "bearish", "neutral"):
            return b
    return "neutral"


def _features_dict(features: dict | None) -> dict:
    if not features:
        return {}
    if isinstance(features.get("features_json"), str):
        try:
            parsed = json.loads(features["features_json"])
            if isinstance(parsed, dict):
                return {**features, **parsed}
        except Exception:
            pass
    return features


def _derive_setup_class(setup_candidates: list[dict] | None) -> str:
    """Setup class of the leading candidate, or the explicit no-setup marker.

    Defaulting to ``technical_dealer_watch`` made the degraded fallback the
    system's identity whenever no candidate existed at all, which is a
    different condition entirely and must stay distinguishable (P1-1).
    """
    if setup_candidates:
        sc = setup_candidates[0].get("setup_class")
        if sc:
            return str(sc)
    return NO_SETUP_CLASS


def _derive_regime(features: dict, ranking: dict | None) -> str:
    if features.get("regime"):
        return str(features["regime"]).lower()
    if ranking and ranking.get("regime"):
        return str(ranking["regime"]).lower()
    return "unknown"


def _has_acceptance(ranking: dict | None, features: dict) -> bool:
    if not ranking:
        return False
    spot = ranking.get("spot") or features.get("spot")
    dealer = ranking.get("dealer_structure") or {}
    call_wall = dealer.get("call_wall") or ranking.get("call_wall")
    if spot is None or call_wall is None:
        return False
    try:
        return float(spot) >= float(call_wall)
    except (TypeError, ValueError):
        return False


def _moneyness_from_contracts(contracts: dict | None) -> str:
    if not contracts:
        return "unknown"
    best = contracts.get("best") if isinstance(contracts, dict) else None
    if not best:
        return "unknown"
    m = best.get("moneyness")
    if m:
        return str(m).lower()
    return "unknown"


def _dte_from_contracts(contracts: dict | None) -> str:
    if not contracts:
        return "unknown"
    best = contracts.get("best") if isinstance(contracts, dict) else None
    if not best:
        return "unknown"
    return _bucket_dte(best.get("dte"))


def _supporting_factors(matrix: AgreementMatrix, bias: str, features: dict, regime: str) -> list[str]:
    out = []
    if matrix.technical >= 0.65:
        out.append("technicals_aligned")
    if matrix.dealer >= 0.65:
        out.append("dealer_structure_aligned")
    if matrix.macro >= 0.65:
        out.append("macro_supportive")
    if matrix.memory >= 0.6:
        out.append("memory_supportive")
    if matrix.expectancy >= 0.6:
        out.append("expectancy_positive")
    if (features or {}).get("ema_stack") == EMAStack.BULLISH.value and bias == "bullish":
        out.append("bullish_ema_stack")
    if regime in ("acceleration", "trending_up") and bias == "bullish":
        out.append("acceleration_regime")
    return out


def _rejection_factors(matrix: AgreementMatrix, conflicts: list[str]) -> list[str]:
    out = list(conflicts)
    if matrix.technical < 0.4:
        out.append("technicals_misaligned")
    if matrix.dealer < 0.4:
        out.append("dealer_structure_unfavorable")
    if matrix.contracts < 0.4:
        out.append("contracts_quality_low")
    return list(dict.fromkeys(out))


def _build_required_trigger(
    bias: str,
    has_acceptance: bool,
    ranking: dict | None,
    features: dict,
    conflicts: list[str],
) -> TriggerSpec | None:
    dealer = (ranking or {}).get("dealer_structure") or {}
    call_wall = dealer.get("call_wall") or (ranking or {}).get("call_wall")
    put_wall = dealer.get("put_wall") or (ranking or {}).get("put_wall")
    king_node = dealer.get("king_node") or (ranking or {}).get("king_node")

    if bias == "bullish" and not has_acceptance and call_wall is not None:
        return TriggerSpec(type="acceptance_above", level=float(call_wall), condition="spot must accept above call wall")
    if bias == "bearish" and put_wall is not None:
        return TriggerSpec(type="rejection_below", level=float(put_wall), condition="spot must reject below put wall")
    if "low_rvol_entry" in conflicts:
        return TriggerSpec(type="volume_confirm", level=None, condition="RVOL > 0.5x avg")
    if king_node is not None and not has_acceptance and bias == "bullish":
        return TriggerSpec(type="acceptance_above", level=float(king_node), condition="spot must accept above king node")
    return None


def _build_kill_switch(bias: str, ranking: dict | None) -> TriggerSpec | None:
    dealer = (ranking or {}).get("dealer_structure") or {}
    put_wall = dealer.get("put_wall") or (ranking or {}).get("put_wall")
    call_wall = dealer.get("call_wall") or (ranking or {}).get("call_wall")
    if bias == "bullish" and put_wall is not None:
        return TriggerSpec(type="rejection_below", level=float(put_wall))
    if bias == "bearish" and call_wall is not None:
        return TriggerSpec(type="acceptance_above", level=float(call_wall))
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
    symbol = symbol.upper()
    feats = _features_dict(features)
    bias = _derive_bias(ranking, setup_candidates)
    setup_class = _derive_setup_class(setup_candidates)
    regime = _derive_regime(feats, ranking)
    has_acc = _has_acceptance(ranking, feats)
    direction = "long" if bias == "bullish" else ("short" if bias == "bearish" else "neutral")
    dte_bucket = _dte_from_contracts(contracts)
    moneyness = _moneyness_from_contracts(contracts)

    expectancy_mod = compute_expectancy_modifier(conn, setup_class, regime, direction, dte_bucket, moneyness)
    memory_pen = compute_memory_penalty(conn, symbol, setup_class)
    memory_penalty = memory_pen["total_penalty"]

    matrix = AgreementMatrix(
        technical=scoring.score_technical_agreement(feats, bias),
        dealer=scoring.score_dealer_agreement(ranking, bias),
        contracts=scoring.score_contract_agreement(contracts, bias),
        macro=scoring.score_macro_agreement(macro_brief, bias),
        memory=scoring.score_memory_agreement(memory_summary, bias, setup_class),
        expectancy=scoring.score_expectancy_agreement(
            {"expectancy_modifier": expectancy_mod} if expectancy_mod != 0.0 else expectancy_data,
            setup_class,
            regime,
        ),
    )
    conflicts = scoring.detect_conflicts(ranking, feats, macro_brief, contracts, bias)
    avg = matrix.average()

    confidence = max(0.0, min(1.0, avg * (1.0 - memory_penalty * 0.5)))
    bucket = _confidence_bucket(confidence)

    role = determine_contract_role(matrix, conflicts, expectancy_mod, memory_penalty)
    # None when adaptive_expectancy is empty: a size derived from confidence
    # alone is not a size, and consumers must render "unsized" rather than a
    # number that looks calibrated (P1-5).
    has_exp = has_expectancy_history(conn)
    sizing = compute_sizing_modifier(confidence, avg, memory_penalty, regime, has_acc, has_expectancy_data=has_exp)

    required_trigger = _build_required_trigger(bias, has_acc, ranking, feats, conflicts)
    kill_switch = _build_kill_switch(bias, ranking)

    if role == "no_trade" or avg < 0.35:
        decision = "no_trade"
    elif required_trigger is not None and avg < 0.70:
        decision = "wait_for_trigger"
    elif avg >= 0.70 and not any(c in {"no_liquid_contracts"} for c in conflicts):
        decision = "trade"
    elif avg >= 0.35:
        decision = "reduce_only"
    else:
        decision = "no_trade"

    warnings: list[str] = []
    if memory_penalty >= 0.15:
        warnings.append("memory_penalty_active")
    for r in memory_pen.get("reasons", []):
        warnings.append(f"memory:{r}")
    if not has_acc and bias == "bullish":
        warnings.append("s2_state_not_above_call_wall")
    if not has_exp:
        warnings.append("unsized_no_expectancy_data")
    if setup_class == DEGRADED_SETUP_CLASS:
        # The degraded fallback is not a peer of the eight real evaluators;
        # consumers must be able to branch on that rather than reading it as an
        # ordinary setup class (P1-1).
        warnings.append("degraded_setup_class")

    supporting = _supporting_factors(matrix, bias, feats, regime)
    rejection = _rejection_factors(matrix, conflicts)

    hold_policy = HoldPolicy()
    if role == "convex":
        hold_policy.max_contracts_to_runner = 2

    inputs_summary = {
        "has_ranking": ranking is not None,
        "has_features": bool(feats),
        "has_contracts": contracts is not None,
        "has_macro": macro_brief is not None,
        "has_memory": memory_summary is not None,
        "has_rs": rs_candidate is not None,
        "has_narrative_tags": bool(narrative_tags),
        "regime": regime,
        "has_acceptance": has_acc,
        "expectancy_modifier": expectancy_mod,
        "memory_penalty": memory_penalty,
        "agreement_avg": avg,
        "setup_class": setup_class,
        "bias": bias,
        "dte_bucket": dte_bucket,
        "moneyness": moneyness,
    }

    now = utc_now_iso()
    result = ArbResult(
        symbol=symbol,
        generated_at=now,
        arb_decision=decision,
        final_bias=bias,
        confidence=confidence,
        confidence_bucket=bucket,
        setup_class=setup_class,
        agreement_matrix=matrix,
        conflicts=conflicts,
        required_trigger=required_trigger,
        kill_switch=kill_switch,
        approved_contract_role=role,
        sizing_modifier=sizing,
        hold_policy=hold_policy,
        warnings=warnings,
        supporting_factors=supporting,
        rejection_factors=rejection,
        inputs_summary=inputs_summary,
    )

    _persist(conn, result)
    return result


def _persist(conn: sqlite3.Connection, result: ArbResult) -> None:
    arb_id = str(uuid.uuid4())
    try:
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
                json.dumps(result.agreement_matrix.__dict__),
                json.dumps(result.conflicts),
                json.dumps(result.required_trigger.__dict__) if result.required_trigger else None,
                json.dumps(result.kill_switch.__dict__) if result.kill_switch else None,
                result.approved_contract_role,
                result.sizing_modifier,
                json.dumps(result.hold_policy.__dict__),
                json.dumps(result.warnings),
                json.dumps(result.supporting_factors),
                json.dumps(result.rejection_factors),
                json.dumps(result.inputs_summary, default=str),
                result.generated_at,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        # Previously `pass`. A silent failure here stops the reasoning log
        # being written while every caller still sees a normal ArbResult, so
        # the record an autonomous system is audited against can disappear
        # with no signal at all. Counted and surfaced via
        # `persistence_failure_count` so diagnostics can assert on it (P2-4).
        global _PERSIST_FAILURES
        _PERSIST_FAILURES += 1
        logger.exception(
            "arbitration persistence failed symbol=%s arb_id=%s (total failures=%s)",
            result.symbol,
            arb_id,
            _PERSIST_FAILURES,
        )
