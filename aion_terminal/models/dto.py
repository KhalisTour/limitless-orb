from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ContractDTO:
    symbol: str
    option_symbol: str
    strike: float
    expiry: str | None
    type: str | None
    gamma: float
    open_interest: int
    iv: float
    dte: int
    underlying_price: float
    timestamp: str


@dataclass(slots=True)
class LevelsDTO:
    symbol: str
    spot: float
    king_node: float
    call_wall: float | None
    put_wall: float | None
    flip_zone: float | None
    acceleration_zones: list[float] = field(default_factory=list)
    regime: str = "range"
    distances: dict[str, float | None] = field(default_factory=dict)
    curve: list[dict[str, Any]] = field(default_factory=list)
