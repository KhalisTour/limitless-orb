from aion_terminal.features.dealer import compute_levels


def test_compute_levels_empty_contracts_defaults_to_spot():
    levels = compute_levels([], 100.0, symbol="SPY")
    assert levels["king_node"] == 100.0
    assert levels["symbol"] == "SPY"
