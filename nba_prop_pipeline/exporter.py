from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd


DEFAULT_EXPORT_COLUMNS: Iterable[str] = [
    "PLAYER_ID",
    "PLAYER_NAME",
    "TEAM_ABBREVIATION",
    "OPPONENT_ABBREVIATION",
    "GAME_ID",
    "GAME_DATETIME",
    "projected_minutes",
    "possessions_per_game",
    "usage_rate",
    "touches",
    "time_of_poss",
    "potential_assists",
    "rebound_chances",
    "fga",
    "fg3a",
    "fg_pct",
    "fg3_pct",
    "pnr_proxy",
    "opp_assist_factor",
    "opp_points_factor",
    "opp_reb_factor",
    "opp_3pa_factor",
    "points_proj",
    "assists_proj",
    "rebounds_proj",
    "threes_proj",
    "P_points_ge_20",
    "P_assists_ge_6",
    "P_rebounds_ge_8",
    "P_3pm_ge_3",
    "MC_P_points_ge_20",
    "MC_P_assists_ge_6",
    "MC_P_rebounds_ge_8",
    "MC_P_3pm_ge_3",
]


def export_json(df: pd.DataFrame, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [col for col in DEFAULT_EXPORT_COLUMNS if col in df.columns]
    records = df[columns].to_dict(orient="records")
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    return output_path
