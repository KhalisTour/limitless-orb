from __future__ import annotations

from aion_terminal.features.technical import (
    build_technical_features,
    classify_ema_stack,
    compute_compression,
    compute_rvol,
)
from aion_terminal.models.dto import UnderlyingBarRecord


def _bar(idx: int, close: float = 100.0, volume: int = 1000) -> UnderlyingBarRecord:
    return UnderlyingBarRecord(
        symbol="AAPL",
        timeframe="1D",
        bar_ts=f"2026-04-{idx + 1:02d}",
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
        vwap=close,
    )


def test_ema_stack_bullish():
    assert classify_ema_stack(105, 102, 98) == "bullish_stack"


def test_ema_stack_bearish():
    assert classify_ema_stack(95, 98, 102) == "bearish_stack"


def test_ema_stack_mixed():
    assert classify_ema_stack(100, 102, 98) == "mixed"


def test_rvol_returns_one_when_insufficient_history():
    bars = [_bar(i, close=100 + i) for i in range(5)]
    assert compute_rvol(bars, lookback=20) == 1.0


def test_rvol_elevated():
    bars = [_bar(i, volume=1000) for i in range(25)]
    bars[-1] = _bar(24, volume=3000)
    assert abs(compute_rvol(bars, lookback=20) - 3.0) < 1e-9


def test_compression_detected():
    closes = [100.0 + (i * 0.15) for i in range(10)]
    result = compute_compression(closes, window=10)
    assert result["compressed"] is True


def test_compression_not_detected():
    closes = [100.0 + (i * (10.0 / 9.0)) for i in range(10)]
    result = compute_compression(closes, window=10)
    assert result["compressed"] is False


def test_build_technical_features_empty_bars_returns_safe_defaults():
    features, _ = build_technical_features([], "1D")
    assert features.close == 0.0
