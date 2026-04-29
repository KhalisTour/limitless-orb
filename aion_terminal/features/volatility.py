"""Volatility feature extraction utilities."""
from __future__ import annotations

import math
from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(var)


def _true_range(curr: dict[str, Any], prev_close: float | None) -> float:
    high = _as_float(curr.get("high"))
    low = _as_float(curr.get("low"))
    if prev_close is None:
        return max(0.0, high - low)
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _avg(values: list[float]) -> float:
    return (sum(values) / len(values)) if values else 0.0


def compute_volatility_features(bars: list[dict]) -> dict:
    if len(bars) < 2:
        return {
            "realized_vol": 0.0,
            "atr": 0.0,
            "compression_ratio": 1.0,
            "volatility_state": "normal",
            "expansion_signal": False,
        }

    closes = [_as_float(b.get("close")) for b in bars]
    returns: list[float] = []
    for idx in range(1, len(closes)):
        prev = closes[idx - 1]
        curr = closes[idx]
        if prev > 0:
            returns.append((curr - prev) / prev)
    realized_vol = _stdev(returns)

    tr_values: list[float] = []
    prev_close: float | None = None
    for bar in bars:
        tr_values.append(_true_range(bar, prev_close))
        prev_close = _as_float(bar.get("close"))

    atr_window = 14
    recent_window = min(5, len(tr_values))
    recent_tr = tr_values[-recent_window:]
    hist_tr = tr_values[-atr_window:] if len(tr_values) >= atr_window else tr_values

    atr = _avg(hist_tr)
    recent_atr = _avg(recent_tr)
    historical_atr = _avg(tr_values[:-recent_window]) if len(tr_values) > recent_window else atr
    if historical_atr <= 0.0:
        compression_ratio = 1.0
    else:
        compression_ratio = recent_atr / historical_atr

    expansion_signal = compression_ratio >= 1.35
    avg_close = _avg(closes)
    atr_pct = (atr / avg_close) if avg_close > 0 else 0.0
    ultra_quiet = realized_vol <= 0.0005 and atr_pct <= 0.005
    if (compression_ratio <= 0.75 or ultra_quiet) and not expansion_signal:
        volatility_state = "compressed"
    elif expansion_signal:
        volatility_state = "expanding"
    else:
        volatility_state = "normal"

    return {
        "realized_vol": realized_vol,
        "atr": atr,
        "compression_ratio": compression_ratio,
        "volatility_state": volatility_state,
        "expansion_signal": expansion_signal,
    }


def is_squeeze(bars: list[dict]) -> bool:
    features = compute_volatility_features(bars)
    return features["volatility_state"] == "compressed"
