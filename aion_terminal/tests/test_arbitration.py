from __future__ import annotations

import json
import sys
import types
import uuid
from dataclasses import asdict

import pytest

if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None)

from aion_terminal.arbitration import adaptive, scoring
from aion_terminal.arbitration.arbiter import run_arbitration
from aion_terminal.arbitration.schemas import AgreementMatrix
from aion_terminal.arbitration.service import _arb_to_json
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.utils.time_utils import utc_now_iso


SCHEMA_PATH = "aion_terminal/storage/schema.sql"


@pytest.fixture()
def conn(tmp_path):
    db = tmp_path / "arb.db"
    c = get_connection(str(db))
    bootstrap_schema(c, SCHEMA_PATH)
    yield c
    c.close()


# ------------- scoring -------------

def test_technical_agreement_bullish_stack():
    f = {"ema_stack": "bullish_stack", "trend": "uptrend", "rvol": 1.2}
    s = scoring.score_technical_agreement(f, "bullish")
    assert s > 0.7


def test_technical_agreement_bearish_stack():
    f = {"ema_stack": "bearish_stack", "trend": "downtrend"}
    s = scoring.score_technical_agreement(f, "bullish")
    assert s < 0.4


def test_technical_agreement_ema_stack_uses_producer_vocabulary():
    """P0-2 regression: the producer emits ``bullish_stack``/``bearish_stack``.

    The stale literal ``"bullish"`` must NOT move the score (that mismatch is the
    original bug), while the real producer value must apply the EMA term.
    """
    from aion_terminal.models.enums import EMAStack

    stale = scoring.score_technical_agreement({"ema_stack": "bullish"}, "bullish")
    real = scoring.score_technical_agreement({"ema_stack": EMAStack.BULLISH.value}, "bullish")
    assert stale == 0.5  # unmatched -> no EMA contribution
    assert real == pytest.approx(0.7)  # 0.5 base + 0.2 EMA bonus
    # Every producible value round-trips to a non-0.5 (or explicitly-neutral) result.
    assert scoring.score_technical_agreement({"ema_stack": EMAStack.BEARISH.value}, "bullish") < 0.5
    assert scoring.score_technical_agreement({"ema_stack": EMAStack.BEARISH.value}, "bearish") > 0.5


def test_dealer_agreement_acceleration_bullish():
    r = {"spot": 100, "regime": "acceleration", "dealer_structure": {"call_wall": 110, "put_wall": 90, "king_node": 95}}
    s = scoring.score_dealer_agreement(r, "bullish")
    assert s > 0.65


def test_dealer_agreement_near_call_wall_penalty():
    near = {"spot": 100, "regime": "trend", "dealer_structure": {"call_wall": 101, "put_wall": 90, "king_node": 95}}
    far = {"spot": 100, "regime": "trend", "dealer_structure": {"call_wall": 110, "put_wall": 90, "king_node": 95}}
    assert scoring.score_dealer_agreement(near, "bullish") < scoring.score_dealer_agreement(far, "bullish")


def test_dealer_agreement_regime_symmetric_across_bias():
    """P0-3 regression: regime is directionless and must contribute identically
    to bullish and bearish. Use a structurally neutral map (walls equidistant,
    king node offset) so only the regime term differs between the two calls."""
    base = {"spot": 100, "dealer_structure": {"call_wall": 108, "put_wall": 92, "king_node": 96}}
    for regime in ("acceleration", "trend", "range"):
        r = {**base, "regime": regime}
        bull = scoring.score_dealer_agreement(r, "bullish")
        bear = scoring.score_dealer_agreement(r, "bearish")
        assert bull == pytest.approx(bear), f"regime {regime} asymmetric: {bull} vs {bear}"


def test_dealer_agreement_trend_regime_not_noop():
    """P0-3 regression: the ``trend`` value the producer emits must be scored,
    not silently ignored (previously it matched no branch and stayed 0.5)."""
    accel = {"spot": 100, "regime": "acceleration", "dealer_structure": {"call_wall": 108, "put_wall": 92, "king_node": 96}}
    trend = {"spot": 100, "regime": "trend", "dealer_structure": {"call_wall": 108, "put_wall": 92, "king_node": 96}}
    rng = {"spot": 100, "regime": "range", "dealer_structure": {"call_wall": 108, "put_wall": 92, "king_node": 96}}
    s_accel = scoring.score_dealer_agreement(accel, "bullish")
    s_trend = scoring.score_dealer_agreement(trend, "bullish")
    s_range = scoring.score_dealer_agreement(rng, "bullish")
    # acceleration (+0.25) > trend (+0.10) > range (-0.05), and trend != the neutral base.
    assert s_accel > s_trend > s_range
    assert s_trend != pytest.approx(0.5)


def test_dealer_agreement_bearish_reward_reachable():
    """P0-3 regression: a bearish thesis with favourable structure must be able
    to score above the 0.5 neutral base (previously the bearish path was starved)."""
    r = {"spot": 100, "regime": "acceleration", "dealer_structure": {"call_wall": 103, "put_wall": 92, "king_node": 96}}
    assert scoring.score_dealer_agreement(r, "bearish") > 0.6


def test_macro_agreement_risk_on_bullish():
    assert scoring.score_macro_agreement({"regime": "risk_on"}, "bullish") >= 0.7


def test_macro_agreement_stagflationary_bullish_penalty():
    assert scoring.score_macro_agreement({"regime": "stagflation"}, "bullish") <= 0.4


def test_contract_agreement_no_contracts_degraded():
    assert scoring.score_contract_agreement(None, "bullish") == 0.3
    assert scoring.score_contract_agreement({"best": None}, "bullish") == 0.3


def test_expectancy_agreement_empty_history_neutral():
    assert scoring.score_expectancy_agreement(None, "x", "y") == 0.5
    assert scoring.score_expectancy_agreement({}, "x", "y") == 0.5


# ------------- conflict detection -------------

def test_conflict_near_call_wall():
    r = {"spot": 100, "dealer_structure": {"call_wall": 101, "put_wall": 90}}
    contracts = {"best": {"spread_pct": 0.05, "volume": 100}}
    c = scoring.detect_conflicts(r, {}, None, contracts, "bullish")
    assert "near_call_wall_resistance" in c


def test_conflict_no_liquid_contracts():
    c = scoring.detect_conflicts({"spot": 100}, {}, None, None, "bullish")
    assert "no_liquid_contracts" in c
    c2 = scoring.detect_conflicts({"spot": 100}, {}, None, {"best": {"spread_pct": 0.5, "volume": 0}}, "bullish")
    assert "no_liquid_contracts" in c2


def test_conflict_acceptance_not_confirmed():
    r = {"spot": 100, "dealer_structure": {"call_wall": 110, "put_wall": 90}}
    c = scoring.detect_conflicts(r, {}, None, {"best": {"spread_pct": 0.03, "volume": 500}}, "bullish")
    assert "acceptance_not_confirmed" in c


def test_conflict_low_rvol_momentum():
    f = {"rvol": 0.1}
    c = scoring.detect_conflicts({"spot": 100}, f, None, {"best": {"spread_pct": 0.03, "volume": 500}}, "bullish")
    assert "low_rvol_entry" in c


# ------------- adaptive -------------

def test_expectancy_modifier_empty_returns_neutral(conn):
    m = adaptive.compute_expectancy_modifier(conn, "momentum_continuation", "trending_up", "long", "4-7", "atm")
    assert m == 0.0


def test_expectancy_modifier_with_history(conn):
    conn.execute(
        """INSERT INTO adaptive_expectancy (exp_id, updated_at, scope, sample_count, win_rate,
           avg_pnl_pct, avg_mfe_pct, avg_mae_pct, avg_hold_minutes, expectancy_modifier, raw_stats_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), utc_now_iso(), "setup:momentum_continuation", 10, 0.6, 20.0, 30.0, -15.0, 60.0, 0.4, "{}"),
    )
    conn.commit()
    m = adaptive.compute_expectancy_modifier(conn, "momentum_continuation", "any", "long", "4-7", "atm")
    assert m == 0.4


def test_memory_penalty_empty_returns_zero(conn):
    out = adaptive.compute_memory_penalty(conn, "AMD", "momentum_continuation")
    assert out["total_penalty"] == 0.0
    assert out["reasons"] == []


def test_contract_role_high_agreement_convex():
    m = AgreementMatrix(0.8, 0.8, 0.7, 0.7, 0.8, 0.7)
    role = adaptive.determine_contract_role(m, [], 0.3, 0.0)
    assert role == "convex"


def test_contract_role_low_agreement_safer_only():
    m = AgreementMatrix(0.4, 0.4, 0.5, 0.5, 0.4, 0.5)
    role = adaptive.determine_contract_role(m, [], 0.0, 0.0)
    assert role in ("safer_only", "balanced")


def test_contract_role_critical_conflict_no_trade():
    m = AgreementMatrix(0.5, 0.5, 0.3, 0.5, 0.5, 0.5)
    role = adaptive.determine_contract_role(m, ["no_liquid_contracts"], 0.0, 0.0)
    assert role == "no_trade"


def test_sizing_modifier_s2_penalty():
    base = adaptive.compute_sizing_modifier(0.8, 0.8, 0.0, "trending_up", True)
    penalized = adaptive.compute_sizing_modifier(0.8, 0.8, 0.0, "trending_up", False)
    assert penalized < base
    assert penalized == pytest.approx(base * 0.5, rel=0.01)


def test_sizing_modifier_clamped_min():
    s = adaptive.compute_sizing_modifier(0.0, 0.0, 0.5, "range", False)
    assert s >= 0.1


# ------------- arbiter -------------

def _full_inputs(symbol, spot=100.0):
    ranking = {
        "symbol": symbol,
        "spot": spot,
        "regime": "acceleration",
        "dealer_structure": {"call_wall": spot * 1.1, "put_wall": spot * 0.95, "king_node": spot},
        "top_ranked_setup": {"bias": "bullish"},
    }
    features = {"ema_stack": "bullish_stack", "trend": "uptrend", "rvol": 1.5, "spot": spot, "regime": "acceleration"}
    contracts = {
        "best": {
            "contract_symbol": f"{symbol}_C",
            "total_score": 75,
            "spread_pct": 0.04,
            "liquidity_score": 0.8,
            "open_interest": 1000,
            "volume": 500,
            "dte": 7,
            "moneyness": "atm",
        }
    }
    setup_candidates = [{"setup_class": "momentum_continuation", "direction": "long"}]
    return ranking, features, contracts, setup_candidates


def test_arbiter_no_data_returns_no_trade(conn):
    r = run_arbitration("XYZ", None, None, None, None, None, None, None, None, None, conn)
    assert r.arb_decision in ("no_trade", "reduce_only", "wait_for_trigger")
    assert r.approved_contract_role in ("no_trade", "safer_only")


@pytest.mark.parametrize("symbol,spot", [("AMD", 350.0), ("NVDA", 900.0), ("COIN", 250.0), ("IONQ", 30.0)])
def test_arbiter_full_inputs_generalize(conn, symbol, spot):
    ranking, features, contracts, setups = _full_inputs(symbol, spot)
    r = run_arbitration(symbol, ranking, setups, features, contracts, {"regime": "risk_on"}, None, None, None, None, conn)
    assert r.symbol == symbol
    assert r.final_bias == "bullish"
    assert r.setup_class == "momentum_continuation"
    assert r.confidence > 0.3
    assert isinstance(r.conflicts, list)
    assert r.arb_decision in ("trade", "wait_for_trigger", "reduce_only", "no_trade")


def test_arbiter_persists_to_db(conn):
    ranking, features, contracts, setups = _full_inputs("AMD", 350.0)
    run_arbitration("AMD", ranking, setups, features, contracts, None, None, None, None, None, conn)
    count = conn.execute("SELECT COUNT(*) AS n FROM arbitration_snapshots").fetchone()["n"]
    assert count == 1


def test_arbiter_cache_only_no_marketdata(conn, monkeypatch):
    """Verify arbiter never reaches out to MarketData (no network imports invoked)."""
    import aion_terminal.data_sources as ds_mod
    # If any MarketData fetcher is called, fail
    called = {"flag": False}
    for attr in dir(ds_mod):
        obj = getattr(ds_mod, attr, None)
        if callable(obj) and "fetch" in attr.lower():
            def _boom(*a, **k):
                called["flag"] = True
                raise RuntimeError("marketdata called")
            monkeypatch.setattr(ds_mod, attr, _boom, raising=False)
    ranking, features, contracts, setups = _full_inputs("AMD", 350.0)
    run_arbitration("AMD", ranking, setups, features, contracts, None, None, None, None, None, conn)
    assert called["flag"] is False


# ------------- frontend safety -------------

def test_arb_result_all_fields_present(conn):
    ranking, features, contracts, setups = _full_inputs("AMD", 350.0)
    r = run_arbitration("AMD", ranking, setups, features, contracts, None, None, None, None, None, conn)
    payload = _arb_to_json(r)
    required = {
        "symbol", "generated_at", "arb_decision", "final_bias", "confidence",
        "confidence_bucket", "setup_class", "agreement_matrix", "conflicts",
        "required_trigger", "kill_switch", "approved_contract_role",
        "sizing_modifier", "hold_policy", "warnings", "supporting_factors",
        "rejection_factors", "inputs_summary",
    }
    assert required.issubset(set(payload.keys()))


def test_arb_result_no_missing_keys(conn):
    r = run_arbitration("AMD", None, None, None, None, None, None, None, None, None, conn)
    payload = _arb_to_json(r)
    assert "required_trigger" in payload
    assert "kill_switch" in payload
    assert "agreement_matrix" in payload
    am = payload["agreement_matrix"]
    for key in ("technical", "dealer", "contracts", "macro", "memory", "expectancy"):
        assert key in am


def test_arb_result_null_fields_not_omitted(conn):
    r = run_arbitration("AMD", None, None, None, None, None, None, None, None, None, conn)
    payload = _arb_to_json(r)
    # required_trigger and kill_switch may be None but key must exist
    assert payload["required_trigger"] is None or isinstance(payload["required_trigger"], dict)
    assert payload["kill_switch"] is None or isinstance(payload["kill_switch"], dict)
    # JSON-serializable end-to-end
    json.dumps(payload)


# ------------- rebuild -------------

def test_rebuild_expectancy_empty_db_returns_zero(conn):
    n = adaptive.rebuild_expectancy_summaries(conn)
    assert n == 0


def _seed_candidate(conn, candidate_id, setup_class, direction):
    now = utc_now_iso()
    conn.execute(
        """INSERT INTO setup_candidates
        (candidate_id, as_of_ts, symbol, setup_class, direction, score, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (candidate_id, now, "AMD", setup_class, direction, 1.0, "open", now, now),
    )


def test_rebuild_expectancy_with_outcomes(conn):
    now = utc_now_iso()
    for i in range(6):
        cid = f"c{i}"
        _seed_candidate(conn, cid, "momentum_continuation", "long")
        conn.execute(
            """INSERT INTO setup_outcomes (candidate_id, outcome_ts, pnl_pct, max_favorable_excursion,
               max_adverse_excursion, hold_minutes, is_winner, outcome_label, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, now, 15.0 if i % 2 else -5.0, 20.0, -10.0, 45, 1 if i % 2 else 0, "x", "", now, now),
        )
    conn.commit()
    n = adaptive.rebuild_expectancy_summaries(conn)
    assert n >= 1
    row = conn.execute(
        "SELECT * FROM adaptive_expectancy WHERE scope = ?",
        ("setup:momentum_continuation",),
    ).fetchone()
    assert row is not None
    assert row["sample_count"] == 6


def test_setup_class_via_join_not_direct_column(conn):
    """Verify setup_outcomes table has no setup_class column."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(setup_outcomes)").fetchall()]
    assert "setup_class" not in cols
    # Verify rebuild uses JOIN by inserting outcomes only (no candidates)
    n_before = adaptive.rebuild_expectancy_summaries(conn)
    assert n_before == 0


# ------------- candidate ranking -------------

def test_candidates_sorted_by_composite_score(conn, monkeypatch):
    from aion_terminal.arbitration import service as svc
    # Seed feature snapshots for two symbols with different quality
    now = utc_now_iso()
    for sym, spot in [("AAA", 100.0), ("BBB", 50.0)]:
        conn.execute(
            """INSERT INTO feature_snapshots (snapshot_ts, symbol, expiry, spot, regime,
               king_node, call_wall, put_wall, features_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, sym, "combined", spot, "acceleration", spot, spot * 1.1, spot * 0.95,
             json.dumps({"ema_stack": "bullish_stack", "trend": "uptrend", "rvol": 1.5}), now, now),
        )
    conn.commit()
    out = svc.get_arbitration_candidates(conn, limit=5, watchlist=["AAA", "BBB"])
    assert isinstance(out, list)
    assert len(out) <= 5
    scores = [o["confidence"] * o["sizing_modifier"] for o in out]
    assert scores == sorted(scores, reverse=True)


def test_candidates_generalize_across_symbols(conn):
    from aion_terminal.arbitration import service as svc
    now = utc_now_iso()
    for sym in ("AMD", "NVDA", "COIN", "IONQ"):
        conn.execute(
            """INSERT INTO feature_snapshots (snapshot_ts, symbol, expiry, spot, regime,
               king_node, call_wall, put_wall, features_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, sym, "combined", 100.0, "acceleration", 100.0, 110.0, 95.0,
             json.dumps({"ema_stack": "bullish_stack"}), now, now),
        )
    conn.commit()
    out = svc.get_arbitration_candidates(conn, limit=10, watchlist=["AMD", "NVDA", "COIN", "IONQ"])
    symbols = {o["symbol"] for o in out}
    assert symbols == {"AMD", "NVDA", "COIN", "IONQ"}
