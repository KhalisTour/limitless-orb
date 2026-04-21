from __future__ import annotations

from aion_terminal.features.dealer import compute_combined_and_expiry_levels


def test_compute_combined_and_expiry_levels_produces_expiry_maps():
    grouped_chain = {
        "2026-04-10": {
            "contracts": [
                {"option_symbol": "OPT1", "side": "call", "strike": 100, "gamma": 0.2, "open_interest": 100, "iv": 0.2, "dte": 1},
                {"option_symbol": "OPT2", "side": "put", "strike": 95, "gamma": 0.1, "open_interest": 200, "iv": 0.3, "dte": 1},
            ]
        },
        "2026-04-17": {
            "contracts": [
                {"option_symbol": "OPT3", "side": "call", "strike": 105, "gamma": 0.3, "open_interest": 150, "iv": 0.2, "dte": 8}
            ]
        },
    }

    combined, expiry_levels = compute_combined_and_expiry_levels(grouped_chain, spot=100.0, symbol="AAPL")

    assert "2026-04-10" in expiry_levels
    assert "2026-04-17" in expiry_levels
    assert isinstance(combined.get("curve"), list)
    assert "king_node" in expiry_levels["2026-04-10"]
    assert "regime" in expiry_levels["2026-04-17"]
