from __future__ import annotations

from enum import Enum


class Regime(str, Enum):
    RANGE = "range"
    TREND = "trend"
    ACCELERATION = "acceleration"


class EMAStack(str, Enum):
    """Canonical EMA-alignment vocabulary.

    Single source of truth for the string values produced by
    ``features.technical.classify_ema_stack`` and consumed by the
    arbitration scoring layer. Do not re-declare these literals elsewhere.
    """

    BULLISH = "bullish_stack"
    BEARISH = "bearish_stack"
    MIXED = "mixed"


class TrendState(str, Enum):
    """Canonical trend vocabulary.

    Single source of truth for the values produced by
    ``features.technical.classify_trend`` and consumed by the arbitration
    scoring layer. The ``STRONG_*`` variants are the highest-conviction
    readings and must be matched wherever the plain variants are — scoring
    that only recognises ``uptrend``/``downtrend`` silently discards exactly
    the setups it should weigh most.
    """

    STRONG_UP = "strong_uptrend"
    UP = "uptrend"
    STRONG_DOWN = "strong_downtrend"
    DOWN = "downtrend"
    NEUTRAL = "neutral"

    @classmethod
    def bullish_values(cls) -> frozenset[str]:
        return frozenset({cls.STRONG_UP.value, cls.UP.value})

    @classmethod
    def bearish_values(cls) -> frozenset[str]:
        return frozenset({cls.STRONG_DOWN.value, cls.DOWN.value})


class AlertType(str, Enum):
    REGIME_CHANGE = "REGIME_CHANGE"
    KING_NODE_SHIFT = "KING_NODE_SHIFT"
    CALL_WALL_HIT = "CALL_WALL_HIT"
    PUT_WALL_HIT = "PUT_WALL_HIT"
    ACCELERATION_ZONE_ENTRY = "ACCELERATION_ZONE_ENTRY"
