from __future__ import annotations

from dataclasses import dataclass

from aion_terminal.data.rs_engine import MIN_CONDITIONS_TO_PASS, RSFeatures
from aion_terminal.utils.time_utils import utc_now_iso


@dataclass
class LeadershipEvent:
    ticker: str
    event_type: str
    rs_percentile: float
    conditions_met: int
    close: float
    return_1y_pct: float
    detected_at: str


def detect_leadership_events(
    features: list[RSFeatures],
    min_percentile: float = 75.0,
) -> list[LeadershipEvent]:
    priority_1: list[LeadershipEvent] = []
    priority_2: list[LeadershipEvent] = []
    priority_3: list[LeadershipEvent] = []

    for f in features:
        if f.rs_new_high_before_price:
            priority_1.append(
                LeadershipEvent(
                    ticker=f.ticker,
                    event_type="rs_new_high_before_price",
                    rs_percentile=f.rs_percentile,
                    conditions_met=f.conditions_met,
                    close=f.close,
                    return_1y_pct=f.return_1y_pct,
                    detected_at=utc_now_iso(),
                )
            )

        if f.rs_new_high_63d and f.rs_percentile >= min_percentile:
            priority_2.append(
                LeadershipEvent(
                    ticker=f.ticker,
                    event_type="rs_new_high",
                    rs_percentile=f.rs_percentile,
                    conditions_met=f.conditions_met,
                    close=f.close,
                    return_1y_pct=f.return_1y_pct,
                    detected_at=utc_now_iso(),
                )
            )

        if f.conditions_met >= MIN_CONDITIONS_TO_PASS and f.rs_percentile < min_percentile:
            priority_3.append(
                LeadershipEvent(
                    ticker=f.ticker,
                    event_type="rs_emerging",
                    rs_percentile=f.rs_percentile,
                    conditions_met=f.conditions_met,
                    close=f.close,
                    return_1y_pct=f.return_1y_pct,
                    detected_at=utc_now_iso(),
                )
            )

    priority_1.sort(key=lambda e: e.rs_percentile, reverse=True)
    priority_2.sort(key=lambda e: e.rs_percentile, reverse=True)
    priority_3.sort(key=lambda e: e.rs_percentile, reverse=True)

    return [*priority_1, *priority_2, *priority_3]
