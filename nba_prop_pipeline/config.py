from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict


@dataclass(frozen=True)
class PipelineConfig:
    season: str = "2025-26"
    season_type: str = "Regular Season"
    min_games_started: int = 35
    slate_date: date | None = None
    cache_dir: Path = Path("cache")
    output_json: Path = Path("output/daily_player_props.json")
    timeout_seconds: int = 60
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    today_scoreboard_url: str = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
    assist_trap_blitz_weight: float = 0.35

    @property
    def scoreboard_url(self) -> str:
        if self.slate_date is None:
            return self.today_scoreboard_url
        date_string = self.slate_date.strftime("%Y%m%d")
        return f"https://cdn.nba.com/static/json/liveData/scoreboard/{date_string}/scoreboard_00.json"

    assist_hedge_weight: float = 0.18
    scoring_trap_blitz_weight: float = -0.12
    rebound_stretch_big_weight: float = 0.14
    blowout_minutes_penalty_weight: float = 1.0
    foul_minutes_penalty_weight: float = 1.0
    league_avg_trap_blitz_rate: float = 20.0
    league_avg_hedge_rate: float = 18.0

    nba_headers: Dict[str, str] = field(
        default_factory=lambda: {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Origin": "https://www.nba.com",
            "Referer": "https://www.nba.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "x-nba-stats-origin": "stats",
            "x-nba-stats-token": "true",
        }
    )

    @property
    def request_headers(self) -> Dict[str, str]:
        merged = dict(self.nba_headers)
        merged["User-Agent"] = self.user_agent
        return merged