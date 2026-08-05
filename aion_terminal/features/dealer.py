from __future__ import annotations

from typing import Any

from aion_terminal.models.enums import GammaState, Regime, StructuralBias
from aion_terminal.utils.math_utils import as_float, as_int


# Acceleration is measured as a fraction of spot, not an absolute dollar
# distance. The previous hardcoded $2.50 band meant +/-0.6% on a $400 name and
# +/-12.5% on a $20 one, so the regime classifier meant something different for
# every symbol.
ACCELERATION_PROXIMITY_PCT = 1.5
KING_PIN_PCT = 3.0
WALL_PROXIMITY_PCT = 2.0

# A vacuum is a *relative* absence of gamma near spot: price sitting in a trough
# between concentrations. It is measured as mean |net exposure| within the
# proximity band divided by the curve's peak exposure.
#
# The original test asked whether *any single strike* below 10% of peak sat
# within $2.50 of spot. On a dense ladder that is almost always true, so
# "acceleration" fired on ~72% of symbols and carried almost no information.
# Averaging across the band and comparing to the peak restores the contrast.
#
# Thresholds are calibrated against the observed distribution of that ratio
# across the chain snapshots on hand (p25 0.056, p50 0.136, p75 0.465). They are
# heuristic: re-derive them from realised outcomes once setup_outcomes is
# populated, together with the arbiter's 0.70 trade threshold.
VACUUM_PEAK_RATIO = 0.10
PIN_PEAK_RATIO = 0.50


def _find_flip_zone(curve: list[dict[str, Any]]) -> tuple[float | None, str]:
    """Locate the gamma flip zone by interpolating the cumulative zero crossing.

    Returns ``(flip_zone, status)``. Snapping to whichever bracketing strike had
    the smaller magnitude put the flip on a strike that is merely *near* the
    crossing, which on a wide ladder can be far from it. Interpolating between
    the bracketing strikes places it where the cumulative curve actually crosses
    zero.

    ``status`` is explicit rather than a bare ``None``: downstream consumers
    treat a missing flip zone as "not near it" and silently disable themselves,
    so the absence of a crossing must be distinguishable from a failure to look.
    """
    if len(curve) < 2:
        return None, "insufficient_curve"

    cumulative = 0.0
    prev_cumulative: float | None = None
    prev_strike: float | None = None

    for point in curve:
        cumulative += as_float(point["net_exposure"])
        strike = as_float(point["strike"])

        if cumulative == 0.0:
            return strike, "exact_zero"

        if prev_cumulative is not None and prev_strike is not None:
            crossed = (prev_cumulative < 0.0 < cumulative) or (prev_cumulative > 0.0 > cumulative)
            if crossed:
                span = abs(prev_cumulative) + abs(cumulative)
                if span == 0.0:
                    return prev_strike, "interpolated"
                # Linear interpolation to the zero crossing between the two
                # bracketing strikes.
                flip = prev_strike + (strike - prev_strike) * (abs(prev_cumulative) / span)
                return flip, "interpolated"

        prev_cumulative = cumulative
        prev_strike = strike

    return None, "no_crossing_in_range"


def _local_gamma_ratio(curve: list[dict[str, Any]], spot: float) -> float | None:
    """Mean |net exposure| near spot as a fraction of the curve's peak.

    The peak is the reference because a vacuum is defined relative to the
    concentrations bracketing it. The curve median is unusable here: it is
    dominated by far-OTM strikes carrying almost no gamma, so the ATM band
    always towers over it (observed minimum ratio 1.69) and nothing is ever
    classified a vacuum.

    Returns ``None`` when no strike falls inside the proximity band.
    """
    if not curve or spot <= 0.0:
        return None

    peak = max((abs(as_float(p["net_exposure"])) for p in curve), default=0.0)
    if peak <= 0.0:
        return None

    near = [
        abs(as_float(p["net_exposure"]))
        for p in curve
        if abs(as_float(p["strike"]) - spot) / spot * 100.0 <= ACCELERATION_PROXIMITY_PCT
    ]
    if not near:
        return None
    return (sum(near) / len(near)) / peak


def _classify_structure(
    spot: float,
    king_node: float,
    call_wall: float | None,
    put_wall: float | None,
    curve: list[dict[str, Any]],
) -> dict[str, Any]:
    """Decompose dealer structure into an unsigned state and a signed bias.

    The single ``regime`` string conflated two independent questions: how price
    moves (unsigned) and which way the structure leans (signed). A gamma vacuum
    accelerates whichever direction price is already travelling, so reading it
    as directional handed one side a free bias.
    """
    if spot <= 0.0:
        return {
            "gamma_state": GammaState.NORMAL.value,
            "structural_bias": StructuralBias.NEUTRAL.value,
            "king_proximity_pct": 0.0,
            "call_wall_proximity_pct": None,
            "put_wall_proximity_pct": None,
            "local_gamma_ratio": None,
        }

    king_proximity_pct = abs(spot - king_node) / spot * 100.0 if king_node else 0.0
    cw_pct = ((as_float(call_wall) - spot) / spot * 100.0) if call_wall is not None else None
    pw_pct = ((spot - as_float(put_wall)) / spot * 100.0) if put_wall is not None else None

    # Unsigned: is price in a vacuum, pinned to a node, or neither? Measured as
    # gamma density near spot relative to the curve's median.
    ratio = _local_gamma_ratio(curve, spot)

    if ratio is None:
        # No strike near spot at all — nothing to say about local structure.
        gamma_state = GammaState.NORMAL.value
    elif ratio < VACUUM_PEAK_RATIO:
        gamma_state = GammaState.VACUUM.value
    elif ratio >= PIN_PEAK_RATIO or king_proximity_pct < KING_PIN_PCT:
        gamma_state = GammaState.PINNED.value
    else:
        gamma_state = GammaState.NORMAL.value

    # Signed: which side is the structure actually leaning toward? Capped means
    # resistance is close overhead while support is not close below; supported
    # is the mirror image. Ambiguous configurations stay neutral rather than
    # defaulting to a direction.
    capped = cw_pct is not None and 0.0 <= cw_pct < WALL_PROXIMITY_PCT
    supported = pw_pct is not None and 0.0 <= pw_pct < WALL_PROXIMITY_PCT
    if capped and not supported:
        structural_bias = StructuralBias.CAPPED.value
    elif supported and not capped:
        structural_bias = StructuralBias.SUPPORTED.value
    else:
        structural_bias = StructuralBias.NEUTRAL.value

    return {
        "gamma_state": gamma_state,
        "structural_bias": structural_bias,
        "king_proximity_pct": king_proximity_pct,
        "call_wall_proximity_pct": cw_pct,
        "put_wall_proximity_pct": pw_pct,
        "local_gamma_ratio": ratio,
    }


def _compute_levels_core(normalized_contracts: list[dict[str, Any]], spot: float, symbol: str = "") -> dict[str, Any]:
    resolved_symbol = symbol or (normalized_contracts[0].get("symbol") if normalized_contracts else "")
    curve_map: dict[float, float] = {}

    for contract in normalized_contracts:
        strike = as_float(contract.get("strike"))
        gamma = as_float(contract.get("gamma"))
        open_interest = as_int(contract.get("open_interest"))
        # AUDIT FIX: field name corrected — uses "type" key (SELECT_LATEST_CHAIN
        # aliases side AS type; grouped_chain consumers map side→type before
        # calling _compute_levels_core).
        option_type = (contract.get("type") or "").lower()

        exposure = gamma * open_interest * 100.0
        signed_exposure = exposure if option_type == "call" else -exposure
        curve_map[strike] = curve_map.get(strike, 0.0) + signed_exposure

    curve = [{"strike": strike, "net_exposure": curve_map[strike]} for strike in sorted(curve_map.keys())]

    king_node = as_float(max(curve, key=lambda x: abs(x["net_exposure"]))["strike"]) if curve else as_float(spot)

    call_candidates = [item for item in curve if item["strike"] > spot and item["net_exposure"] > 0.0]
    call_wall = as_float(max(call_candidates, key=lambda x: x["net_exposure"])["strike"]) if call_candidates else None

    put_candidates = [item for item in curve if item["strike"] < spot and item["net_exposure"] < 0.0]
    put_wall = as_float(min(put_candidates, key=lambda x: x["net_exposure"])["strike"]) if put_candidates else None

    flip_zone, flip_zone_status = _find_flip_zone(curve)

    max_abs_exposure = max((abs(as_float(item["net_exposure"])) for item in curve), default=0.0)
    acceleration_threshold = max_abs_exposure * 0.1
    acceleration_zones = [
        as_float(item["strike"]) for item in curve if abs(as_float(item["net_exposure"])) < acceleration_threshold
    ] if max_abs_exposure > 0.0 else []

    dist_call = (as_float(call_wall) - spot) if call_wall is not None else None
    dist_put = (spot - as_float(put_wall)) if put_wall is not None else None
    dist_king = abs(spot - king_node)

    dist_call_pct = (dist_call / spot * 100.0) if (dist_call is not None and spot != 0.0) else None
    dist_put_pct = (dist_put / spot * 100.0) if (dist_put is not None and spot != 0.0) else None
    dist_king_pct = (dist_king / spot * 100.0) if spot != 0.0 else 0.0

    structure = _classify_structure(spot, king_node, call_wall, put_wall, curve)

    # `regime` is retained for existing consumers and derived from the same
    # decomposition so the two can never disagree. New code should read
    # `gamma_state` (unsigned) and `structural_bias` (signed) instead.
    if structure["gamma_state"] == GammaState.VACUUM.value:
        regime = Regime.ACCELERATION.value
    elif structure["gamma_state"] == GammaState.PINNED.value:
        regime = Regime.RANGE.value
    else:
        regime = Regime.TREND.value

    return {
        "symbol": resolved_symbol,
        "spot": as_float(spot),
        "king_node": king_node,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "flip_zone": flip_zone,
        "flip_zone_status": flip_zone_status,
        "acceleration_zones": acceleration_zones,
        "regime": regime,
        **structure,
        "distances": {
            "call_wall": dist_call,
            "put_wall": dist_put,
            "king": dist_king,
            "call_wall_pct": dist_call_pct,
            "put_wall_pct": dist_put_pct,
            "king_pct": dist_king_pct,
        },
        "curve": curve,
    }


def compute_levels(contracts: list[dict[str, Any]], spot: float, symbol: str = "") -> dict[str, Any]:
    """Legacy/compatibility combined dealer map from flat contract rows."""
    normalized = [
        {
            "symbol": c.get("symbol", symbol),
            "strike": c.get("strike"),
            "gamma": c.get("gamma"),
            "open_interest": c.get("open_interest"),
            "type": c.get("type"),
        }
        for c in contracts
    ]
    return _compute_levels_core(normalized, spot=spot, symbol=symbol)


def compute_combined_and_expiry_levels(
    grouped_chain: dict[str, dict[str, Any]],
    spot: float,
    symbol: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Compute combined and per-expiry dealer maps from grouped chain source-of-truth."""
    expiry_levels: dict[str, dict[str, Any]] = {}
    combined_contracts: list[dict[str, Any]] = []

    for expiry, block in grouped_chain.items():
        contracts = [
            {
                "symbol": symbol,
                "strike": c.get("strike"),
                "gamma": c.get("gamma"),
                "open_interest": c.get("open_interest"),
                "type": c.get("side"),
            }
            for c in block.get("contracts", [])
        ]
        expiry_level = _compute_levels_core(contracts, spot=spot, symbol=symbol)
        expiry_level["expiry"] = expiry
        expiry_levels[expiry] = expiry_level
        combined_contracts.extend(contracts)

    combined_levels = _compute_levels_core(combined_contracts, spot=spot, symbol=symbol)
    return combined_levels, expiry_levels
