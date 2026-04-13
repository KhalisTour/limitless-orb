from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict


@dataclass(frozen=True)
class PipelineConfig:
    season: str = "2025-26"
    season_type: str = "Regular Season"
    min_games_started: int = 35
    cache_dir: Path = Path("cache")
    output_json: Path = Path("output/daily_player_props.json")
    timeout_seconds: int = 30
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    today_scoreboard_url: str = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"

    nba_headers: Dict[str, str] = field(
        default_factory=lambda: {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "Host": "stats.nba.com",
            "Origin": "https://www.nba.com",
            "Referer": "https://www.nba.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }
    )

    @property
    def request_headers(self) -> Dict[str, str]:
        merged = dict(self.nba_headers)
        merged["User-Agent"] = self.user_agent
        return merged
