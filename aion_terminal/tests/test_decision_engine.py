from aion_terminal.decision_engine.engine import compute_decision_engine_state


def test_decision_engine_s1_classification():
    out = compute_decision_engine_state(105, bars={"recent_swing_high": 100, "recent_swing_low": 90}, technical_state={"close_above_s1": True, "retest_hold": True, "rvol": 2.0})
    assert out["state"] == "S1"


def test_decision_engine_s2_classification():
    out = compute_decision_engine_state(95, bars={"recent_swing_high": 100, "recent_swing_low": 90})
    assert out["state"] == "S2"


def test_decision_engine_s3_classification():
    out = compute_decision_engine_state(85, bars={"recent_swing_high": 100, "recent_swing_low": 90})
    assert out["state"] == "S3"


def test_ev_scoring_directionality():
    good = compute_decision_engine_state(105, bars={"recent_swing_high": 100, "recent_swing_low": 90}, technical_state={"close_above_s1": True, "rvol": 2.0})
    bad = compute_decision_engine_state(85, bars={"recent_swing_high": 100, "recent_swing_low": 90}, technical_state={"invalidation_triggered": True})
    assert good["ev_score"] > bad["ev_score"]


def test_pin_risk_detection():
    out = compute_decision_engine_state(100, bars={"recent_swing_high": 101, "recent_swing_low": 99}, dealer_structure={"king_node": 100}, chain={"dte": 1})
    assert out["pin_risk"] == "high"


def test_otm_short_dte_s2_trim_bias():
    out = compute_decision_engine_state(95, bars={"recent_swing_high": 100, "recent_swing_low": 90}, chain={"dte": 1, "moneyness": "OTM"})
    assert out["action_bias"] in {"trim", "exit"}


def test_decision_engine_missing_inputs_safe_defaults():
    out = compute_decision_engine_state(100)
    assert out["state"] in {"S1", "S2", "S3"}
    assert "warnings" in out
