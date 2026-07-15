from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from aion_terminal.models.dto import UnderlyingBarRecord
from aion_terminal.models.enums import EMAStack
from aion_terminal.utils.math_utils import as_float, as_int
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)

COMPRESSION_RANGE_PCT_THRESHOLD = 3.0
HIGH_RVOL_THRESHOLD = 1.5
NEAR_LEVEL_PCT_THRESHOLD = 2.0


@dataclass(slots=True)
class TechnicalFeatures:
    symbol: str = ""
    timeframe: str = ""
    computed_at: str = ""
    close: float = 0.0
    vwap: float = 0.0
    vwap_distance_pct: float = 0.0
    ema8: float = 0.0
    ema21: float = 0.0
    ema55: float = 0.0
    ema_stack_state: str = "mixed"
    atr: float = 0.0
    rvol: float = 1.0
    compressed: bool = False
    range_pct: float = 0.0
    resistance: float = 0.0
    support: float = 0.0
    distance_to_resistance_pct: float = 0.0
    distance_to_support_pct: float = 0.0
    impulse_high: float = 0.0
    pullback_pct: float = 0.0
    recovering: bool = False
    trend_state: str = "neutral"


@dataclass(slots=True)
class TechnicalState:
    above_vwap: bool = False
    ema_stack: str = "mixed"
    trend: str = "neutral"
    compressed: bool = False
    high_rvol: bool = False
    near_support: bool = False
    near_resistance: bool = False
    recovering_from_pullback: bool = False
    pullback_depth_pct: float = 0.0


def compute_ema(closes: list[float], period: int) -> list[float]:
    """Compute standard EMA with fixed smoothing and warm-up zeros."""
    if period <= 0:
        return [0.0 for _ in closes]
    if not closes:
        return []

    multiplier = 2.0 / (period + 1)
    out = [0.0 for _ in closes]
    if len(closes) < period:
        return out

    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    prev = seed
    for idx in range(period, len(closes)):
        curr = (closes[idx] - prev) * multiplier + prev
        out[idx] = curr
        prev = curr
    return out


def classify_ema_stack(ema8: float, ema21: float, ema55: float) -> str:
    """Classify EMA alignment as bullish, bearish, or mixed."""
    if ema8 > ema21 > ema55:
        return EMAStack.BULLISH.value
    if ema8 < ema21 < ema55:
        return EMAStack.BEARISH.value
    return EMAStack.MIXED.value


def compute_vwap(bars: list[UnderlyingBarRecord]) -> float:
    """Compute session VWAP across all provided bars."""
    total_volume = 0.0
    total_pv = 0.0
    for bar in bars:
        vol = as_float(bar.volume)
        if vol <= 0.0:
            continue
        typical = (as_float(bar.high) + as_float(bar.low) + as_float(bar.close)) / 3.0
        total_pv += typical * vol
        total_volume += vol

    if total_volume == 0.0:
        return 0.0
    return total_pv / total_volume


def compute_atr(bars: list[UnderlyingBarRecord], period: int = 14) -> float:
    """Compute simple-average ATR using true range."""
    if len(bars) < 2:
        return 0.0

    true_ranges: list[float] = []
    for idx in range(1, len(bars)):
        prev_close = as_float(bars[idx - 1].close)
        high = as_float(bars[idx].high)
        low = as_float(bars[idx].low)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    tail = true_ranges[-period:] if len(true_ranges) >= period else true_ranges
    return sum(tail) / len(tail)


def compute_rvol(bars: list[UnderlyingBarRecord], lookback: int = 20) -> float:
    """Compute RVOL as current volume divided by average previous lookback volumes."""
    if len(bars) < lookback + 1:
        return 1.0

    current = as_float(bars[-1].volume)
    prior = [as_float(b.volume) for b in bars[-(lookback + 1) : -1]]
    avg = sum(prior) / len(prior) if prior else 0.0
    if avg <= 0.0:
        return 1.0
    return current / avg


def compute_compression(closes: list[float], window: int = 10) -> dict[str, Any]:
    """Measure whether recent close range is compressed."""
    if len(closes) < window:
        return {"compressed": False, "range_pct": 0.0, "bars_in_window": min(len(closes), window)}

    segment = closes[-window:]
    low = min(segment)
    high = max(segment)
    if low <= 0.0:
        return {"compressed": False, "range_pct": 0.0, "bars_in_window": window}

    range_pct = (high - low) / low * 100.0
    return {"compressed": range_pct < COMPRESSION_RANGE_PCT_THRESHOLD, "range_pct": range_pct, "bars_in_window": window}


def find_support_resistance(bars: list[UnderlyingBarRecord], lookback: int = 20) -> dict[str, float]:
    """Compute simple support/resistance and distances from current close."""
    if not bars:
        return {
            "resistance": 0.0,
            "support": 0.0,
            "distance_to_resistance_pct": 0.0,
            "distance_to_support_pct": 0.0,
        }

    window = bars[-lookback:] if len(bars) >= lookback else bars
    resistance = max(as_float(b.high) for b in window)
    support = min(as_float(b.low) for b in window)
    close = as_float(bars[-1].close)

    distance_to_resistance_pct = ((resistance - close) / close * 100.0) if close > 0.0 else 0.0
    distance_to_support_pct = ((close - support) / close * 100.0) if close > 0.0 else 0.0

    return {
        "resistance": resistance,
        "support": support,
        "distance_to_resistance_pct": distance_to_resistance_pct,
        "distance_to_support_pct": distance_to_support_pct,
    }


def compute_pullback_depth(bars: list[UnderlyingBarRecord], lookback: int = 20) -> dict[str, Any]:
    """Compute pullback depth after impulse high and whether price is recovering."""
    if len(bars) < 3:
        return {"impulse_high": 0.0, "pullback_low": 0.0, "pullback_pct": 0.0, "recovering": False}

    window = bars[-lookback:] if len(bars) >= lookback else bars
    highs = [as_float(b.high) for b in window]
    impulse_idx = max(range(len(highs)), key=lambda i: highs[i])
    impulse_high = highs[impulse_idx]

    tail = window[impulse_idx:]
    if not tail:
        return {"impulse_high": impulse_high, "pullback_low": impulse_high, "pullback_pct": 0.0, "recovering": False}

    pullback_low = min(as_float(b.low) for b in tail)
    pullback_pct = ((impulse_high - pullback_low) / impulse_high * 100.0) if impulse_high > 0.0 else 0.0

    current_close = as_float(window[-1].close)
    prev_close = as_float(window[-2].close)
    recovering = current_close > pullback_low and current_close > prev_close

    return {
        "impulse_high": impulse_high,
        "pullback_low": pullback_low,
        "pullback_pct": pullback_pct,
        "recovering": recovering,
    }


def classify_trend(ema8: float, ema21: float, ema55: float, vwap: float, close: float) -> str:
    """Classify directional trend state from moving averages and VWAP."""
    stack = classify_ema_stack(ema8, ema21, ema55)
    above_vwap = close > vwap if vwap > 0.0 else False

    if stack == "bullish_stack" and above_vwap:
        return "strong_uptrend"
    if ema8 > ema21 and above_vwap:
        return "uptrend"
    if stack == "bearish_stack" and not above_vwap:
        return "strong_downtrend"
    if ema8 < ema21 and not above_vwap:
        return "downtrend"
    return "neutral"


def _safe_zero_state(symbol: str, timeframe: str) -> tuple[TechnicalFeatures, TechnicalState]:
    features = TechnicalFeatures(symbol=symbol, timeframe=timeframe, computed_at=utc_now_iso())
    state = TechnicalState()
    return features, state


def build_technical_features(bars: list[UnderlyingBarRecord], timeframe: str) -> tuple[TechnicalFeatures, TechnicalState]:
    """Orchestrate technical feature generation for setup evaluation."""
    if not bars:
        return _safe_zero_state("", timeframe)

    symbol = bars[-1].symbol
    if len(bars) < 55:
        logger.warning("Technical feature build for %s has only %s bars; 55+ is recommended.", symbol, len(bars))

    closes = [as_float(b.close) for b in bars]
    ema8_series = compute_ema(closes, 8)
    ema21_series = compute_ema(closes, 21)
    ema55_series = compute_ema(closes, 55)

    close = closes[-1]
    ema8 = as_float(ema8_series[-1]) if ema8_series else 0.0
    ema21 = as_float(ema21_series[-1]) if ema21_series else 0.0
    ema55 = as_float(ema55_series[-1]) if ema55_series else 0.0

    stack = classify_ema_stack(ema8, ema21, ema55)
    vwap = compute_vwap(bars)
    vwap_distance_pct = ((close - vwap) / vwap * 100.0) if vwap > 0.0 else 0.0
    atr = compute_atr(bars, period=14)
    rvol = compute_rvol(bars, lookback=20)
    compression = compute_compression(closes, window=10)
    sr = find_support_resistance(bars, lookback=20)
    pullback = compute_pullback_depth(bars, lookback=20)
    trend_state = classify_trend(ema8, ema21, ema55, vwap, close)

    features = TechnicalFeatures(
        symbol=symbol,
        timeframe=timeframe,
        computed_at=utc_now_iso(),
        close=close,
        vwap=vwap,
        vwap_distance_pct=vwap_distance_pct,
        ema8=ema8,
        ema21=ema21,
        ema55=ema55,
        ema_stack_state=stack,
        atr=atr,
        rvol=rvol,
        compressed=bool(compression["compressed"]),
        range_pct=as_float(compression["range_pct"]),
        resistance=as_float(sr["resistance"]),
        support=as_float(sr["support"]),
        distance_to_resistance_pct=as_float(sr["distance_to_resistance_pct"]),
        distance_to_support_pct=as_float(sr["distance_to_support_pct"]),
        impulse_high=as_float(pullback["impulse_high"]),
        pullback_pct=as_float(pullback["pullback_pct"]),
        recovering=bool(pullback["recovering"]),
        trend_state=trend_state,
    )

    state = TechnicalState(
        above_vwap=close > vwap if vwap > 0.0 else False,
        ema_stack=stack,
        trend=trend_state,
        compressed=features.compressed,
        high_rvol=rvol >= HIGH_RVOL_THRESHOLD,
        near_support=features.distance_to_support_pct <= NEAR_LEVEL_PCT_THRESHOLD,
        near_resistance=features.distance_to_resistance_pct <= NEAR_LEVEL_PCT_THRESHOLD,
        recovering_from_pullback=features.recovering,
        pullback_depth_pct=features.pullback_pct,
    )

    return features, state
