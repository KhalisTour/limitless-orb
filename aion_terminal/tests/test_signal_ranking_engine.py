from __future__ import annotations

from aion_terminal.models.dto import SetupCandidateRecord
from aion_terminal.signals.ranking import rank_candidates


def _candidate(candidate_id: str, symbol: str, direction: str, confidence: float) -> SetupCandidateRecord:
    return SetupCandidateRecord(
        candidate_id=candidate_id,
        as_of_ts="2026-04-29T00:00:00Z",
        symbol=symbol,
        setup_class="momentum_continuation",
        score=confidence,
        direction=direction,
        confidence=confidence,
    )


def test_high_quality_setup_ranks_above_weak_setup():
    strong = _candidate("c1", "AAPL", "bullish", 0.9)
    weak = _candidate("c2", "MSFT", "bullish", 0.35)

    feature_snapshots = {
        "c1": {"ema_stack": "bullish_stack", "trend": "strong_uptrend", "rvol": 2.1, "compressed": True, "dealer_bias": "bullish"},
        "c2": {"ema_stack": "mixed", "trend": "neutral", "rvol": 0.9, "compressed": False, "dealer_bias": "bullish"},
    }
    contract_scores = {
        "c1": {"contract_symbol": "AAPL240C", "delta_efficiency": 0.85, "gamma_efficiency": 0.7, "theta_burden": 0.2, "liquidity_score": 0.9},
        "c2": {"contract_symbol": "MSFT240C", "delta_efficiency": 0.35, "gamma_efficiency": 0.2, "theta_burden": 0.8, "liquidity_score": 0.4},
    }

    ranked = rank_candidates([weak, strong], feature_snapshots, contract_scores)
    assert ranked[0]["symbol"] == "AAPL"
    assert ranked[0]["ranking_score"] > ranked[1]["ranking_score"]


def test_conflicting_signals_reduce_score():
    aligned = _candidate("c1", "NVDA", "bullish", 0.8)
    conflict = _candidate("c2", "AMD", "bullish", 0.8)

    feature_snapshots = {
        "c1": {"ema_stack": "bullish_stack", "trend": "uptrend", "rvol": 1.6, "dealer_bias": "bullish"},
        "c2": {"ema_stack": "bearish_stack", "trend": "downtrend", "rvol": 1.6, "dealer_bias": "bearish", "pin_risk": 0.8},
    }
    contract_scores = {
        "c1": {"delta_efficiency": 0.7, "gamma_efficiency": 0.6, "theta_burden": 0.3, "liquidity_score": 0.8},
        "c2": {"delta_efficiency": 0.7, "gamma_efficiency": 0.6, "theta_burden": 0.3, "liquidity_score": 0.8},
    }

    ranked = rank_candidates([aligned, conflict], feature_snapshots, contract_scores)
    by_symbol = {row["symbol"]: row for row in ranked}
    assert by_symbol["NVDA"]["ranking_score"] > by_symbol["AMD"]["ranking_score"]
    assert "bias_conflict" in by_symbol["AMD"]["warnings"]


def test_low_liquidity_penalizes_ranking():
    liquid = _candidate("c1", "SPY", "bullish", 0.75)
    illiquid = _candidate("c2", "IWM", "bullish", 0.75)

    feature_snapshots = {
        "c1": {"ema_stack": "bullish_stack", "trend": "uptrend", "rvol": 1.4, "dealer_bias": "bullish"},
        "c2": {"ema_stack": "bullish_stack", "trend": "uptrend", "rvol": 1.4, "dealer_bias": "bullish"},
    }
    contract_scores = {
        "c1": {"delta_efficiency": 0.6, "gamma_efficiency": 0.6, "theta_burden": 0.4, "liquidity_score": 0.85},
        "c2": {"delta_efficiency": 0.6, "gamma_efficiency": 0.6, "theta_burden": 0.4, "liquidity_score": 0.1},
    }

    ranked = rank_candidates([liquid, illiquid], feature_snapshots, contract_scores)
    by_symbol = {row["symbol"]: row for row in ranked}
    assert by_symbol["SPY"]["ranking_score"] > by_symbol["IWM"]["ranking_score"]
    assert "low_liquidity" in by_symbol["IWM"]["warnings"]
