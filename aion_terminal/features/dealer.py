from __future__ import annotations

from typing import Any

from aion_terminal.utils.math_utils import as_float, as_int


def _compute_levels_core(normalized_contracts: list[dict[str, Any]], spot: float, symbol: str = "") -> dict[str, Any]:
    resolved_symbol = symbol or (normalized_contracts[0].get("symbol") if normalized_contracts else "")
    curve_map: dict[float, float] = {}

    for contract in normalized_contracts:
        strike = as_float(contract.get("strike"))
        gamma = as_float(contract.get("gamma"))
        open_interest = as_int(contract.get("open_interest"))
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

    flip_zone = None
    if len(curve) >= 2:
        cumulative = 0.0
        prev_cumulative = None
        prev_strike = None
        for point in curve:
            cumulative += as_float(point["net_exposure"])
            strike = as_float(point["strike"])
            if cumulative == 0.0:
                flip_zone = strike
                break
            if prev_cumulative is not None:
                crossed = (prev_cumulative < 0.0 < cumulative) or (prev_cumulative > 0.0 > cumulative)
                if crossed:
                    flip_zone = prev_strike if abs(prev_cumulative) <= abs(cumulative) else strike
                    break
            prev_cumulative = cumulative
            prev_strike = strike

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

    is_acceleration = any(abs(spot - strike) < 2.5 for strike in acceleration_zones)
    regime = "acceleration" if is_acceleration else ("range" if dist_king_pct < 3.0 else "trend")

    return {
        "symbol": resolved_symbol,
        "spot": as_float(spot),
        "king_node": king_node,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "flip_zone": flip_zone,
        "acceleration_zones": acceleration_zones,
        "regime": regime,
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
