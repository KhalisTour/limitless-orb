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


class AlertType(str, Enum):
    REGIME_CHANGE = "REGIME_CHANGE"
    KING_NODE_SHIFT = "KING_NODE_SHIFT"
    CALL_WALL_HIT = "CALL_WALL_HIT"
    PUT_WALL_HIT = "PUT_WALL_HIT"
    ACCELERATION_ZONE_ENTRY = "ACCELERATION_ZONE_ENTRY"
