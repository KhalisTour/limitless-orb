from __future__ import annotations

from aion_terminal.features.volatility import compute_volatility_features, is_squeeze


def _bar(o: float, h: float, l: float, c: float) -> dict:
    return {"open": o, "high": h, "low": l, "close": c}


def test_flat_price_is_compressed():
    bars = [_bar(100, 100.2, 99.8, 100.0) for _ in range(30)]
    out = compute_volatility_features(bars)
    assert out["volatility_state"] == "compressed"
    assert out["expansion_signal"] is False
    assert is_squeeze(bars) is True


def test_widening_range_is_expanding():
    bars = []
    price = 100.0
    for i in range(30):
        width = 0.3 + (i * 0.25)
        bars.append(_bar(price, price + width, price - width, price + (0.05 * i)))
        price += 0.2

    out = compute_volatility_features(bars)
    assert out["volatility_state"] == "expanding"
    assert out["expansion_signal"] is True


def test_stable_range_is_normal():
    bars = []
    price = 100.0
    for i in range(30):
        width = 1.0 + (0.1 if i % 4 == 0 else 0.0)
        close = price + (0.05 if i % 2 == 0 else -0.05)
        bars.append(_bar(price, price + width, price - width, close))
        price = close

    out = compute_volatility_features(bars)
    assert out["volatility_state"] == "normal"
    assert 0.75 < out["compression_ratio"] < 1.35
