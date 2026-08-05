"""Tests for the decomposed dealer structure read (P0-4) and flip zone (P1-3)."""

from __future__ import annotations

from aion_terminal.features.dealer import (
    _classify_structure,
    _compute_levels_core,
    _find_flip_zone,
    _local_gamma_ratio,
)
from aion_terminal.models.enums import GammaState, Regime, StructuralBias
from aion_terminal.services.ranking_service import infer_bias


def _contract(strike, gamma, oi, side):
    return {"strike": strike, "gamma": gamma, "open_interest": oi, "type": side}


# --------------------------- flip zone (P1-3) ---------------------------


def test_flip_zone_interpolates_between_bracketing_strikes():
    """The crossing sits between strikes, not on whichever one is closer."""
    curve = [
        {"strike": 100.0, "net_exposure": -100.0},
        {"strike": 110.0, "net_exposure": 300.0},
    ]
    flip, status = _find_flip_zone(curve)
    assert status == "interpolated"
    # cumulative: -100 then +200; crossing is 1/3 of the way from 100 to 110.
    assert 103.0 < flip < 104.0
    assert flip not in (100.0, 110.0), "snapped to a strike instead of interpolating"


def test_flip_zone_reports_no_crossing_explicitly():
    """A bare None is indistinguishable from 'never looked'."""
    curve = [
        {"strike": 100.0, "net_exposure": 50.0},
        {"strike": 110.0, "net_exposure": 50.0},
    ]
    flip, status = _find_flip_zone(curve)
    assert flip is None
    assert status == "no_crossing_in_range"


def test_flip_zone_handles_short_curve():
    flip, status = _find_flip_zone([{"strike": 100.0, "net_exposure": 1.0}])
    assert flip is None
    assert status == "insufficient_curve"


def test_flip_zone_exact_zero_is_reported():
    curve = [
        {"strike": 100.0, "net_exposure": 100.0},
        {"strike": 110.0, "net_exposure": -100.0},
    ]
    flip, status = _find_flip_zone(curve)
    assert status == "exact_zero"
    assert flip == 110.0


# ------------------------ gamma state (unsigned) ------------------------


def test_gamma_state_is_scale_invariant():
    """The old $2.50 band meant 0.6% on a $400 name and 12.5% on a $20 one."""
    def build(scale):
        return [
            _contract(90 * scale, 0.01, 1000, "put"),
            _contract(100 * scale, 0.001, 10, "call"),
            _contract(110 * scale, 0.01, 1000, "call"),
        ]

    cheap = _compute_levels_core(build(1), spot=100.0)
    rich = _compute_levels_core(build(10), spot=1000.0)
    assert cheap["gamma_state"] == rich["gamma_state"]


def test_vacuum_requires_low_local_gamma_relative_to_peak():
    """Spot in a trough between two concentrations."""
    contracts = [
        _contract(90.0, 0.05, 5000, "put"),
        _contract(100.0, 0.0001, 1, "call"),
        _contract(110.0, 0.05, 5000, "call"),
    ]
    levels = _compute_levels_core(contracts, spot=100.0)
    assert levels["gamma_state"] == GammaState.VACUUM.value
    assert levels["regime"] == Regime.ACCELERATION.value


def test_dense_gamma_at_spot_is_not_a_vacuum():
    contracts = [
        _contract(99.0, 0.05, 5000, "put"),
        _contract(100.0, 0.05, 6000, "call"),
        _contract(101.0, 0.05, 5000, "call"),
    ]
    levels = _compute_levels_core(contracts, spot=100.0)
    assert levels["gamma_state"] != GammaState.VACUUM.value


def test_local_gamma_ratio_is_none_when_nothing_near_spot():
    curve = [{"strike": 500.0, "net_exposure": 10.0}]
    assert _local_gamma_ratio(curve, spot=100.0) is None


# ---------------------- structural bias (signed) ----------------------


def test_structural_bias_capped_when_resistance_is_close_overhead():
    s = _classify_structure(spot=100.0, king_node=100.0, call_wall=101.0, put_wall=80.0, curve=[])
    assert s["structural_bias"] == StructuralBias.CAPPED.value


def test_structural_bias_supported_when_support_is_close_below():
    s = _classify_structure(spot=100.0, king_node=100.0, call_wall=120.0, put_wall=99.0, curve=[])
    assert s["structural_bias"] == StructuralBias.SUPPORTED.value


def test_structural_bias_neutral_when_both_walls_are_close():
    """Ambiguous structure must not resolve to a direction."""
    s = _classify_structure(spot=100.0, king_node=100.0, call_wall=101.0, put_wall=99.0, curve=[])
    assert s["structural_bias"] == StructuralBias.NEUTRAL.value


def test_structural_bias_neutral_when_walls_are_missing():
    s = _classify_structure(spot=100.0, king_node=100.0, call_wall=None, put_wall=None, curve=[])
    assert s["structural_bias"] == StructuralBias.NEUTRAL.value


def test_gamma_state_never_implies_direction():
    """A vacuum must be reachable with either signed bias."""
    seen = set()
    for cw, pw in ((101.0, 80.0), (120.0, 99.0)):
        s = _classify_structure(
            spot=100.0,
            king_node=100.0,
            call_wall=cw,
            put_wall=pw,
            curve=[
                {"strike": 90.0, "net_exposure": 5000.0},
                {"strike": 100.0, "net_exposure": 1.0},
                {"strike": 110.0, "net_exposure": 5000.0},
            ],
        )
        assert s["gamma_state"] == GammaState.VACUUM.value
        seen.add(s["structural_bias"])
    assert seen == {StructuralBias.CAPPED.value, StructuralBias.SUPPORTED.value}


# ------------------------- infer_bias (P0-4/P0-5) -------------------------


def test_infer_bias_no_longer_returns_bullish_on_missing_put_wall():
    """as_float(None) == 0.0 made `put_wall <= spot` true for every symbol."""
    bias, reasons = infer_bias(dealer_features={"regime": "acceleration", "spot": 100.0, "put_wall": None})
    assert bias == "neutral"
    assert "bias_defaulted" in reasons


def test_infer_bias_does_not_read_direction_from_a_vacuum():
    bias, _ = infer_bias(
        dealer_features={"regime": "acceleration", "spot": 100.0, "put_wall": 90.0, "gamma_state": "vacuum"}
    )
    assert bias == "neutral", "directionless gamma state must not imply a side"


def test_infer_bias_uses_the_signed_read_symmetrically():
    up, _ = infer_bias(dealer_features={"structural_bias": StructuralBias.SUPPORTED.value})
    down, _ = infer_bias(dealer_features={"structural_bias": StructuralBias.CAPPED.value})
    assert (up, down) == ("bullish", "bearish")
