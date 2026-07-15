from __future__ import annotations

from aion_terminal.features.technical import TechnicalFeatures, TechnicalState
from aion_terminal.signals.setups import evaluate_symbol_snapshot


def make_dealer(spot=100.0, king_node=98.0, call_wall=105.0, put_wall=95.0, flip_zone=100.0, regime="trend"):
    return {
        "symbol": "AAPL",
        "spot": spot,
        "king_node": king_node,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "flip_zone": flip_zone,
        "regime": regime,
        "curve": [],
    }


def make_technical(
    above_vwap=True,
    ema_stack="bullish_stack",
    trend="uptrend",
    compressed=False,
    high_rvol=True,
    near_support=False,
    near_resistance=False,
    recovering=False,
    pullback_depth_pct=0.0,
):
    features = TechnicalFeatures(
        symbol="AAPL",
        timeframe="1D",
        close=100.0,
        vwap=99.0 if above_vwap else 101.0,
        support=95.0,
        resistance=105.0,
        compressed=compressed,
        pullback_pct=pullback_depth_pct,
        recovering=recovering,
        trend_state=trend,
        ema_stack_state=ema_stack,
        rvol=2.0 if high_rvol else 1.0,
        distance_to_support_pct=1.0 if near_support else 5.0,
        distance_to_resistance_pct=1.0 if near_resistance else 5.0,
    )
    state = TechnicalState(
        above_vwap=above_vwap,
        ema_stack=ema_stack,
        trend=trend,
        compressed=compressed,
        high_rvol=high_rvol,
        near_support=near_support,
        near_resistance=near_resistance,
        recovering_from_pullback=recovering,
        pullback_depth_pct=pullback_depth_pct,
    )
    return features, state


def make_tags(*keys):
    return [{"tag_key": key} for key in keys]


def _has(signals, setup_class: str, bias: str) -> bool:
    return any(s.setup_class == setup_class and s.bias == bias for s in signals)


def test_pullback_support_bullish_fires():
    dealer = make_dealer(spot=96.0)
    features, state = make_technical(recovering=True, ema_stack="bullish_stack", near_resistance=False)
    signals = evaluate_symbol_snapshot("AAPL", dealer, features, state, make_tags(), min_confidence=0.3)
    assert _has(signals, "pullback_into_support", "bullish")


def test_pullback_support_bearish_fires():
    dealer = make_dealer(spot=104.0)
    features, state = make_technical(above_vwap=False, ema_stack="bearish_stack", trend="downtrend", near_resistance=True)
    signals = evaluate_symbol_snapshot("AAPL", dealer, features, state, make_tags(), min_confidence=0.3)
    assert _has(signals, "pullback_into_support", "bearish")


def test_momentum_continuation_bullish_fires():
    dealer = make_dealer(spot=100.0, call_wall=110.0, regime="trend")
    features, state = make_technical(above_vwap=True, ema_stack="bullish_stack", high_rvol=True)
    signals = evaluate_symbol_snapshot("AAPL", dealer, features, state, make_tags(), min_confidence=0.3)
    assert _has(signals, "momentum_continuation", "bullish")


def test_momentum_continuation_bearish_fires():
    dealer = make_dealer(spot=100.0, put_wall=90.0, regime="trend")
    features, state = make_technical(above_vwap=False, ema_stack="bearish_stack", trend="downtrend", high_rvol=True)
    signals = evaluate_symbol_snapshot("AAPL", dealer, features, state, make_tags(), min_confidence=0.3)
    assert _has(signals, "momentum_continuation", "bearish")


def test_squeeze_unwind_bullish_fires():
    dealer = make_dealer(spot=100.5, flip_zone=100.0)
    features, state = make_technical(compressed=True, ema_stack="mixed", high_rvol=True)
    signals = evaluate_symbol_snapshot("AAPL", dealer, features, state, make_tags(), min_confidence=0.3)
    assert _has(signals, "squeeze_unwind", "bullish")


def test_squeeze_unwind_bearish_fires():
    dealer = make_dealer(spot=99.5, flip_zone=100.0)
    features, state = make_technical(
        above_vwap=False,
        ema_stack="mixed",
        trend="downtrend",
        compressed=True,
        high_rvol=True,
    )
    signals = evaluate_symbol_snapshot("AAPL", dealer, features, state, make_tags(), min_confidence=0.3)
    assert _has(signals, "squeeze_unwind", "bearish")


def test_event_rerating_bullish_fires():
    dealer = make_dealer(spot=102.0, king_node=100.0)
    features, state = make_technical(trend="uptrend")
    signals = evaluate_symbol_snapshot("AAPL", dealer, features, state, make_tags("bullish_catalyst"), min_confidence=0.3)
    assert _has(signals, "event_rerating", "bullish")


def test_event_rerating_bearish_fires():
    dealer = make_dealer(spot=96.0, king_node=100.0)
    features, state = make_technical(above_vwap=False, ema_stack="bearish_stack", trend="downtrend")
    signals = evaluate_symbol_snapshot("AAPL", dealer, features, state, make_tags("bearish_catalyst"), min_confidence=0.3)
    assert _has(signals, "event_rerating", "bearish")


def test_no_signals_when_conditions_not_met():
    dealer = make_dealer(spot=100.0, call_wall=150.0, put_wall=50.0, flip_zone=130.0, regime="range")
    features, state = make_technical(
        above_vwap=False,
        ema_stack="mixed",
        trend="neutral",
        compressed=False,
        high_rvol=False,
        near_support=False,
        near_resistance=False,
        recovering=False,
        pullback_depth_pct=0.0,
    )
    signals = evaluate_symbol_snapshot("AAPL", dealer, features, state, make_tags(), min_confidence=0.3)
    assert signals == []


def test_degraded_watch_setup_bullish_when_near_call_wall():
    dealer = make_dealer(spot=352.68, king_node=355.0, call_wall=355.0, put_wall=337.5, regime="range")
    features, state = make_technical(ema_stack="bullish_stack", trend="strong_uptrend", high_rvol=True, near_resistance=True)
    signals = evaluate_symbol_snapshot("AMD", dealer, features, state, make_tags(), min_confidence=0.0)
    assert signals
    assert signals[0].setup_class == "technical_dealer_watch"
    assert signals[0].bias == "bullish"
    assert 0.2 <= signals[0].confidence_raw <= 0.35
    assert "degraded_setup_candidate" in signals[0].warnings


def test_min_confidence_filters_weak_signals():
    dealer = make_dealer(spot=96.0, king_node=95.5, put_wall=95.0, call_wall=110.0, regime="range")
    features, state = make_technical(
        above_vwap=False,
        ema_stack="mixed",  # preconditions can pass via trend, but strict stack score remains 0.0
        trend="uptrend",
        compressed=False,
        high_rvol=False,
        near_support=False,
        near_resistance=False,
        recovering=True,
        pullback_depth_pct=0.0,
    )
    signals = evaluate_symbol_snapshot("AAPL", dealer, features, state, make_tags(), min_confidence=0.5)
    assert signals == []


# ------------- P0-5: neutral bias must not default long / coerce to puts -------------

import sys as _sys
import types as _types

if "dotenv" not in _sys.modules:
    _sys.modules["dotenv"] = _types.SimpleNamespace(load_dotenv=lambda *a, **k: None)

from aion_terminal.features.contracts import score_and_rank_contracts
from aion_terminal.services.ranking_service import infer_bias
from aion_terminal.storage.db import bootstrap_schema, get_connection


def test_infer_bias_defaults_to_neutral():
    """P0-5 regression: with no directional evidence the system must answer
    'neutral', never fall back to 'bullish'."""
    _features, state = make_technical(ema_stack="mixed", trend="neutral", high_rvol=False)
    dealer = make_dealer(regime="range", put_wall=105.0, spot=100.0)  # put wall above spot
    bias, reasons = infer_bias(technical_state=state, dealer_features=dealer)
    assert bias == "neutral"
    assert "bias_defaulted" in reasons


def test_infer_bias_still_reads_directional_evidence():
    """The neutral default must not swallow a genuine bullish read."""
    _features, state = make_technical(ema_stack="bullish_stack", trend="uptrend")
    bias, reasons = infer_bias(technical_state=state)
    assert bias == "bullish"
    assert reasons == []


def test_score_and_rank_contracts_refuses_neutral(tmp_path):
    """P0-5 regression: a neutral bias must return no recommendation, not silently
    coerce to puts."""
    conn = get_connection(str(tmp_path / "contracts.db"))
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    try:
        rec = score_and_rank_contracts(conn, symbol="AAPL", bias="neutral", spot=100.0)
        assert rec.best is None
        assert rec.safer is None
        assert rec.convex is None
        assert rec.all_scored == []
        assert "neutral_bias_no_recommendation" in rec.warnings
    finally:
        conn.close()
