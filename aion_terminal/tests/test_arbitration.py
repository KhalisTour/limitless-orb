from __future__ import annotations

import json
import uuid
from dataclasses import asdict

import pytest

from aion_terminal.arbitration import scoring, adaptive, arbiter
from aion_terminal.arbitration.adaptive import (
    compute_expectancy_modifier,
    compute_memory_penalty,
    compute_sizing_modifier,
    determine_contract_role,
    rebuild_expectancy_summaries,
)
from aion_terminal.arbitration.arbiter import run_arbitration
from aion_terminal.arbitration.schemas import AgreementMatrix, ArbResult
from aion_terminal.arbitration.scoring import (
    detect_conflicts,
    score_contract_agreement,
    score_dealer_agreement,
    score_expectancy_agreement,
    score_macro_agreement,
    score_memory_agreement,
    score_technical_agreement,
)
from aion_terminal.arbitration.service import get_arbitration_candidates
from aion_terminal.storage.db import bootstrap_schema, get_connection

SCHEMA_PATH = "aion_terminal/storage/schema.sql"


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "arb.db"
    c = get_connection(str(db))
    bootstrap_schema(c, SCHEMA_PATH)
    yield c
    c.close()


# ---------- Agreement scoring ----------
def test_technical_agreement_bullish_stack():
    s = score_technical_agreement({"ema_stack": "bullish_stacked_up", "trend": "up", "rvol": 1.2}, "bullish")
    assert s > 0.7


def test_technical_agreement_bearish_stack():
    s = score_technical_agreement({"ema_stack": "bearish_stacked_down", "trend": "down", "rvol": 1.1}, "bearish")
    assert s > 0.7


def test_dealer_agreement_acceleration_bullish():
    s = score_dealer_agreement({"regime": "acceleration", "call_wall_pct": 6.0, "put_wall_pct": -4.0}, "bullish")
    assert s > 0.7


def test_dealer_agreement_near_call_wall_penalty():
    s = score_dealer_agreement({"regime": "acceleration", "call_wall_pct": 1.0, "put_wall_pct": -3.0}, "bullish")
    assert s < 0.7


def test_macro_agreement_risk_on_bullish():
    assert score_macro_agreement({"regime": "risk-on"}, "bullish") >= 0.8


def test_macro_agreement_stagflationary_bullish_penalty():
    assert score_macro_agreement({"regime": "stagflationary"}, "bullish") <= 0.35


def test_contract_agreement_no_contracts_degraded():
    assert score_contract_agreement(None, "bullish") == 0.3
    assert score_contract_agreement({}, "bullish") == 0.3


def test_expectancy_agreement_empty_history_neutral():
    assert score_expectancy_agreement(None, "any", "any") == 0.5
    assert score_expectancy_agreement({}, "any", "any") == 0.5


# ---------- Conflict detection ----------
def test_conflict_near_call_wall():
    conflicts = detect_conflicts(
        {"call_wall_pct": 1.5, "put_wall_pct": -5, "regime": "acceleration", "spot": 100, "call_wall": 101.5},
        {"ema_stack": "bullish"},
        None,
        {"best": {"spread_pct": 5, "volume": 100}},
        "bullish",
    )
    assert "near_call_wall_resistance" in conflicts


def test_conflict_no_liquid_contracts():
    conflicts = detect_conflicts(None, None, None, None, "bullish")
    assert "no_liquid_contracts" in conflicts
    conflicts2 = detect_conflicts(None, None, None, {"best": {"spread_pct": 30, "volume": 0}}, "bullish")
    assert "no_liquid_contracts" in conflicts2


def test_conflict_acceptance_not_confirmed():
    conflicts = detect_conflicts(
        {"call_wall_pct": 5, "put_wall_pct": -5, "spot": 100, "call_wall": 105, "regime": "acceleration"},
        None, None, {"best": {"spread_pct": 5, "volume": 100}}, "bullish",
    )
    assert "acceptance_not_confirmed" in conflicts


def test_conflict_low_rvol_momentum():
    conflicts = detect_conflicts(
        None,
        {"rvol": 0.1, "setup_class": "momentum_continuation"},
        None,
        {"best": {"spread_pct": 5, "volume": 100}},
        "bullish",
    )
    assert "low_rvol_entry" in conflicts


# ---------- Adaptive engine ----------
def test_expectancy_modifier_empty_returns_neutral(conn):
    assert compute_expectancy_modifier(conn, "momentum_continuation", "acceleration", "bullish", "4-7", "atm") == 0.0


def test_expectancy_modifier_with_history(conn):
    conn.execute(
        """INSERT INTO adaptive_expectancy (exp_id, updated_at, scope, sample_count, expectancy_modifier)
           VALUES (?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), "2026-01-01T00:00:00Z", "setup:momentum|dir:bullish", 10, 0.4),
    )
    conn.commit()
    mod = compute_expectancy_modifier(conn, "momentum", "any", "bullish", "0-3", "atm")
    assert mod == 0.4


def test_memory_penalty_empty_returns_zero(conn):
    res = compute_memory_penalty(conn, "AMD", "momentum")
    assert res["total_penalty"] == 0.0
    assert res["reasons"] == []


def test_contract_role_high_agreement_convex():
    am = AgreementMatrix(0.85, 0.85, 0.8, 0.8, 0.85, 0.8)
    assert determine_contract_role(am, [], 0.2, 0.0) == "convex"


def test_contract_role_low_agreement_safer_only():
    am = AgreementMatrix(0.45, 0.45, 0.45, 0.45, 0.45, 0.45)
    assert determine_contract_role(am, [], 0.0, 0.0) == "safer_only"


def test_contract_role_critical_conflict_no_trade():
    am = AgreementMatrix(0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
    assert determine_contract_role(am, ["no_liquid_contracts"], 0.0, 0.0) == "no_trade"


def test_sizing_modifier_s2_penalty():
    base = compute_sizing_modifier(0.8, 0.8, 0.0, "acceleration", True)
    s2 = compute_sizing_modifier(0.8, 0.8, 0.0, "acceleration", False)
    assert s2 < base


def test_sizing_modifier_clamped_min():
    s = compute_sizing_modifier(0.0, 0.0, 0.5, "range", False)
    assert s >= 0.1


# ---------- Arbiter ----------
def _full_inputs(symbol):
    ranking = {
        "symbol": symbol, "spot": 100.0, "regime": "acceleration",
        "king_node": 100.0, "call_wall": 105.0, "put_wall": 95.0,
        "call_wall_pct": 5.0, "put_wall_pct": -5.0,
    }
    setup_candidates = [{"setup_class": "momentum_continuation", "direction": "bullish"}]
    features = {"ema_stack": "bullish_stacked_up", "trend": "up", "rvol": 1.2, "regime": "acceleration"}
    contracts = {"best": {"spread_pct": 5.0, "volume": 500, "open_interest": 1000, "dte": 7,
                          "moneyness_bucket": "atm", "total_score": 70}}
    macro = {"regime": "risk-on"}
    return ranking, setup_candidates, features, contracts, macro


def test_arbiter_no_data_returns_no_trade(conn):
    res = run_arbitration("AMD", None, None, None, None, None, None, None, None, None, conn)
    assert res.arb_decision == "no_trade"


@pytest.mark.parametrize("symbol", ["AMD", "NVDA", "COIN", "IONQ"])
def test_arbiter_full_inputs(conn, symbol):
    r, sc, f, c, m = _full_inputs(symbol)
    res = run_arbitration(symbol, r, sc, f, c, m, None, None, None, None, conn)
    assert res.symbol == symbol
    assert res.final_bias == "bullish"
    assert res.arb_decision in {"trade", "wait_for_trigger", "reduce_only", "no_trade"}
    assert 0.0 <= res.confidence <= 1.0
    assert res.approved_contract_role in {"convex", "balanced", "safer_only", "no_trade"}


def test_arbiter_full_inputs_amd(conn):
    r, sc, f, c, m = _full_inputs("AMD")
    res = run_arbitration("AMD", r, sc, f, c, m, None, None, None, None, conn)
    assert res.final_bias == "bullish"


def test_arbiter_full_inputs_nvda(conn):
    r, sc, f, c, m = _full_inputs("NVDA")
    res = run_arbitration("NVDA", r, sc, f, c, m, None, None, None, None, conn)
    assert res.final_bias == "bullish"


def test_arbiter_full_inputs_coin(conn):
    r, sc, f, c, m = _full_inputs("COIN")
    res = run_arbitration("COIN", r, sc, f, c, m, None, None, None, None, conn)
    assert res.final_bias == "bullish"


def test_arbiter_full_inputs_ionq(conn):
    r, sc, f, c, m = _full_inputs("IONQ")
    res = run_arbitration("IONQ", r, sc, f, c, m, None, None, None, None, conn)
    assert res.final_bias == "bullish"


def test_arbiter_persists_to_db(conn):
    r, sc, f, c, m = _full_inputs("AMD")
    run_arbitration("AMD", r, sc, f, c, m, None, None, None, None, conn)
    rows = conn.execute("SELECT * FROM arbitration_snapshots WHERE symbol = ?", ("AMD",)).fetchall()
    assert len(rows) == 1


def test_arbiter_cache_only_no_marketdata(conn, monkeypatch):
    """Verify arbiter does not call any MarketData APIs."""
    import aion_terminal.services.contract_service as cs
    import aion_terminal.services.ranking_service as rs

    def boom(*a, **k):
        raise AssertionError("market data should not be called")

    monkeypatch.setattr(cs, "get_contract_recommendation", boom, raising=False)
    monkeypatch.setattr(rs, "rank_symbol", boom, raising=False)
    r, sc, f, c, m = _full_inputs("AMD")
    res = run_arbitration("AMD", r, sc, f, c, m, None, None, None, None, conn)
    assert res.symbol == "AMD"


# ---------- Frontend safety ----------
def test_arb_result_all_fields_present(conn):
    r, sc, f, c, m = _full_inputs("AMD")
    res = run_arbitration("AMD", r, sc, f, c, m, None, None, None, None, conn)
    d = asdict(res)
    expected = {
        "symbol", "generated_at", "arb_decision", "final_bias", "confidence",
        "confidence_bucket", "setup_class", "agreement_matrix", "conflicts",
        "required_trigger", "kill_switch", "approved_contract_role",
        "sizing_modifier", "hold_policy", "warnings", "supporting_factors",
        "rejection_factors", "inputs_summary",
    }
    assert expected.issubset(set(d.keys()))


def test_arb_result_no_missing_keys(conn):
    res = run_arbitration("AMD", None, None, None, None, None, None, None, None, None, conn)
    d = asdict(res)
    serialized = json.dumps(d, default=str)
    parsed = json.loads(serialized)
    assert "agreement_matrix" in parsed
    assert "technical" in parsed["agreement_matrix"]


def test_arb_result_null_fields_not_omitted(conn):
    res = run_arbitration("AMD", None, None, None, None, None, None, None, None, None, conn)
    d = asdict(res)
    assert "required_trigger" in d
    assert "kill_switch" in d


# ---------- Rebuild ----------
def test_rebuild_expectancy_empty_db_returns_zero(conn):
    assert rebuild_expectancy_summaries(conn) == 0


def _seed_outcomes(conn, n=6, win=True):
    now = "2026-01-01T00:00:00Z"
    for i in range(n):
        cid = f"c-{uuid.uuid4()}"
        conn.execute(
            """INSERT INTO setup_candidates (candidate_id, as_of_ts, symbol, setup_class, direction,
               score, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, now, "AMD", "momentum_continuation", "bullish", 50, now, now),
        )
        conn.execute(
            """INSERT INTO setup_outcomes (candidate_id, outcome_ts, pnl_pct, max_favorable_excursion,
               max_adverse_excursion, hold_minutes, is_winner, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, now, 5.0 if win else -5.0, 10.0, -3.0, 60, 1 if win else 0, now, now),
        )
    conn.commit()


def test_rebuild_expectancy_with_outcomes(conn):
    _seed_outcomes(conn, 6, win=True)
    updated = rebuild_expectancy_summaries(conn)
    assert updated >= 1
    rows = conn.execute("SELECT * FROM adaptive_expectancy").fetchall()
    assert len(rows) >= 1


def test_setup_class_via_join_not_direct_column(conn):
    """Verify rebuild uses JOIN — setup_outcomes has no setup_class column."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(setup_outcomes)").fetchall()]
    assert "setup_class" not in cols
    _seed_outcomes(conn, 6, win=True)
    rebuild_expectancy_summaries(conn)
    rows = conn.execute("SELECT scope FROM adaptive_expectancy").fetchall()
    assert any("momentum_continuation" in r["scope"] for r in rows)


# ---------- Candidate ranking ----------
def test_candidates_sorted_by_composite_score(conn, monkeypatch):
    from aion_terminal.app import config as cfg
    monkeypatch.setattr(cfg.settings, "watchlist", ["AMD", "NVDA"], raising=False)
    out = get_arbitration_candidates(conn, limit=5)
    if len(out) >= 2:
        scores = [c.get("composite_score", 0.0) for c in out]
        assert scores == sorted(scores, reverse=True)


def test_candidates_generalize_across_symbols(conn, monkeypatch):
    from aion_terminal.app import config as cfg
    monkeypatch.setattr(cfg.settings, "watchlist", ["AMD", "NVDA", "COIN", "IONQ"], raising=False)
    out = get_arbitration_candidates(conn, limit=10)
    assert isinstance(out, list)
    for entry in out:
        assert entry.get("symbol") in {"AMD", "NVDA", "COIN", "IONQ"}
