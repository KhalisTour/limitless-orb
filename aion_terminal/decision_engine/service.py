from __future__ import annotations

from typing import Any

from aion_terminal.decision_engine.engine import compute_decision_engine_state


def _first_dict(*vals: Any) -> dict:
    for val in vals:
        if isinstance(val, dict):
            return val
    return {}


def _first_list(*vals: Any) -> list:
    for val in vals:
        if isinstance(val, list):
            return val
    return []


def run_decision_engine(symbol: str, context_payload: dict) -> dict:
    try:
        payload = context_payload or {}

        rankings_payload = _first_dict(payload.get("rankings_payload"))
        selected_setup = _first_dict(payload.get("selected_setup"))
        setup_candidates = _first_dict(payload.get("setup_candidates"))
        contract_reco = _first_dict(payload.get("contract_recommendations"), rankings_payload.get("contract_recommendations"))

        dealer_structure = _first_dict(payload.get("dealer_structure"), rankings_payload.get("dealer_structure"), selected_setup.get("dealer_structure"))
        technical_state = _first_dict(payload.get("technical_state"), rankings_payload.get("technical_state"), selected_setup.get("technical_state"))

        current_positions = _first_list(payload.get("current_positions"), payload.get("positions"))
        position = current_positions[0] if current_positions and isinstance(current_positions[0], dict) else {}

        spot = (
            payload.get("spot")
            or rankings_payload.get("spot")
            or selected_setup.get("spot")
            or setup_candidates.get("spot")
            or dealer_structure.get("spot")
            or 0.0
        )

        bars = _first_dict(
            payload.get("bars"),
            payload.get("ohlcv"),
            payload.get("underlying_data"),
            rankings_payload.get("bars"),
            selected_setup.get("bars"),
            setup_candidates.get("bars"),
        )

        user_constraints = _first_dict(
            payload.get("user_constraints"),
            payload.get("account"),
            payload.get("account_constraints"),
        )

        return compute_decision_engine_state(
            float(spot or 0.0),
            bars=bars,
            chain=contract_reco,
            position=position,
            technical_state=technical_state,
            dealer_structure=dealer_structure,
            user_constraints=user_constraints,
        )
    except Exception as exc:
        return {
            "state": "S2",
            "ev_score": 0.0,
            "action_bias": "no_trade",
            "warnings": [f"decision_engine_failed: {exc}"],
        }
