from __future__ import annotations

from enum import Enum


class Regime(str, Enum):
    RANGE = "range"
    TREND = "trend"
    ACCELERATION = "acceleration"


class AlertType(str, Enum):
    REGIME_CHANGE = "REGIME_CHANGE"
    KING_NODE_SHIFT = "KING_NODE_SHIFT"
    CALL_WALL_HIT = "CALL_WALL_HIT"
    PUT_WALL_HIT = "PUT_WALL_HIT"
    ACCELERATION_ZONE_ENTRY = "ACCELERATION_ZONE_ENTRY"
