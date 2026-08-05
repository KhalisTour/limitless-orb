"""Regression tests for the technical agreement channel.

Instrumentation over 1,142 historical arbitration rows showed the technical
channel pinned at exactly 0.500 with a single distinct value — it never
influenced a decision. Two independent defects caused that, and both are
guarded here:

1. The producer never persisted the technical read into
   ``feature_snapshots.features_json`` (only ``distances`` was written), so
   the scorer's lookups all returned None.
2. ``classify_trend`` emits ``strong_uptrend``/``strong_downtrend`` but the
   scorer matched only ``uptrend``/``downtrend``, discarding the
   highest-conviction readings.
"""

from __future__ import annotations

import json

from aion_terminal.arbitration.scoring import score_technical_agreement
from aion_terminal.features.technical import TechnicalFeatures, TechnicalState, classify_trend
from aion_terminal.models.enums import EMAStack, TrendState
from aion_terminal.scripts.run_daily_snapshot import _technical_payload


def test_classify_trend_only_emits_known_vocabulary():
    """Every value the producer can emit must be a declared TrendState."""
    produced = {
        classify_trend(3, 2, 1, 1, 5),  # bullish stack, above vwap
        classify_trend(3, 2, 1, 5, 1),  # bullish stack, below vwap
        classify_trend(1, 2, 3, 5, 1),  # bearish stack, below vwap
        classify_trend(1, 2, 3, 1, 5),  # bearish stack, above vwap
        classify_trend(2, 2, 2, 1, 1),  # flat
    }
    assert produced <= {t.value for t in TrendState}


def test_strong_trend_values_are_scored():
    """`strong_uptrend` must move the score, not fall through as a no-op."""
    neutral = {"ema_stack": EMAStack.MIXED.value, "trend": TrendState.NEUTRAL.value}
    strong_up = {"ema_stack": EMAStack.MIXED.value, "trend": TrendState.STRONG_UP.value}
    strong_down = {"ema_stack": EMAStack.MIXED.value, "trend": TrendState.STRONG_DOWN.value}

    base = score_technical_agreement(neutral, "bullish")
    assert score_technical_agreement(strong_up, "bullish") > base
    assert score_technical_agreement(strong_down, "bullish") < base

    base_bear = score_technical_agreement(neutral, "bearish")
    assert score_technical_agreement(strong_down, "bearish") > base_bear
    assert score_technical_agreement(strong_up, "bearish") < base_bear


def test_plain_trend_values_still_scored():
    """The non-strong variants must keep working."""
    neutral = {"ema_stack": EMAStack.MIXED.value, "trend": TrendState.NEUTRAL.value}
    up = {"ema_stack": EMAStack.MIXED.value, "trend": TrendState.UP.value}
    base = score_technical_agreement(neutral, "bullish")
    assert score_technical_agreement(up, "bullish") > base


def _features(**over) -> TechnicalFeatures:
    f = TechnicalFeatures(
        symbol="TEST",
        timeframe="D",
        ema_stack_state=EMAStack.BULLISH.value,
        trend_state=TrendState.STRONG_UP.value,
        rvol=1.4,
        compressed=False,
        pullback_pct=2.0,
        vwap_distance_pct=1.1,
        atr=3.2,
    )
    for k, v in over.items():
        setattr(f, k, v)
    return f


def test_technical_payload_emits_keys_the_scorer_reads():
    """The persisted payload must use the scorer's lookup keys."""
    payload = _technical_payload(_features(), TechnicalState(above_vwap=True))
    for key in ("ema_stack", "trend", "rvol", "compressed", "pullback_depth"):
        assert key in payload, f"scorer reads {key!r} but payload omits it"
    assert payload["ema_stack"] == EMAStack.BULLISH.value
    assert payload["trend"] == TrendState.STRONG_UP.value


def test_pullback_depth_converted_to_fraction():
    """The scorer tests a fraction; the feature is a percentage."""
    payload = _technical_payload(_features(pullback_pct=3.0), TechnicalState())
    assert abs(payload["pullback_depth"] - 0.03) < 1e-9


def test_persisted_payload_moves_the_channel_off_its_default():
    """End-to-end: a real technical read must not score a flat 0.5.

    This is the assertion that fails against the pre-fix producer, which
    wrote only ``{"distances": ...}``.
    """
    payload = _technical_payload(_features(), TechnicalState(above_vwap=True))
    round_tripped = json.loads(json.dumps({"distances": {}, **payload}))

    aligned = score_technical_agreement(round_tripped, "bullish")
    opposed = score_technical_agreement(round_tripped, "bearish")

    assert aligned != 0.5, "technical channel is still pinned at its default"
    assert aligned > 0.5 < 1.0
    assert opposed < 0.5
    assert aligned > opposed


def test_distances_only_payload_scores_the_default():
    """The pre-fix shape is what a dead channel looks like — documented."""
    assert score_technical_agreement({"distances": {"call_wall": 1.2}}, "bullish") == 0.5
