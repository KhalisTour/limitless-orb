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


@dataclass(slots=True)
class RawChainSnapshotRecord:
    snapshot_ts: str
    symbol: str
    option_symbol: str
    expiry: str | None = None
    side: str | None = None
    strike: float | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    mark: float | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None
    open_interest: int | None = None
    volume: int | None = None
    dte: int | None = None
    underlying_price: float | None = None
    source: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class UnderlyingBarRecord:
    symbol: str
    timeframe: str
    bar_ts: str
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None
    vwap: float | None = None
    source: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class FeatureSnapshotRecord:
    snapshot_ts: str
    symbol: str
    expiry: str = "combined"
    dte: int | None = None
    spot: float | None = None
    regime: str | None = None
    king_node: float | None = None
    call_wall: float | None = None
    put_wall: float | None = None
    flip_zone: float | None = None
    features_json: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class SetupCandidateRecord:
    candidate_id: str
    as_of_ts: str
    symbol: str
    setup_class: str
    score: float
    direction: str | None = None
    timeframe: str | None = None
    expiry: str | None = None
    option_type: str | None = None
    strike: float | None = None
    rank: int | None = None
    confidence: float | None = None
    rationale_json: str | None = None
    status: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class SetupOutcomeRecord:
    candidate_id: str
    outcome_ts: str
    pnl_abs: float | None = None
    pnl_pct: float | None = None
    max_favorable_excursion: float | None = None
    max_adverse_excursion: float | None = None
    hold_minutes: int | None = None
    is_winner: int | None = None
    outcome_label: str | None = None
    notes: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class ManualNarrativeTagRecord:
    symbol: str
    tag_date: str
    tag_key: str
    tag_value: str | None = None
    context_json: str | None = None
    created_at: str = ""
    updated_at: str = ""
