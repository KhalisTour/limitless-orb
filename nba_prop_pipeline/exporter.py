from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd
import numpy as np

def _sanitize_for_export(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure JSON-safe numeric output by replacing NaN/None with 0."""
    return (
        df.replace([np.inf, -np.inf], 0)  # optional but safe
          .fillna(0)
    )

DEFAULT_EXPORT_COLUMNS: Iterable[str] = [
    "PLAYER_ID",
    "PLAYER_NAME",
    "TEAM_ABBREVIATION",
    "OPPONENT_ABBREVIATION",
    "GAME_ID",
    "GAME_DATETIME",
    "projected_minutes",
    "recent_min_avg",
    "recent_min_std",
    "recent_pts_avg",
    "recent_ast_avg",
    "recent_reb_avg",
    "recent_fg3m_avg",
    "recent_fg3m_std",
    "recent_games",
    "minutes_source",
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
    "OPP_FG3_PCT",
    "zone_points_raw",
    "team_strategy_factor",
    "dampened_strategy_factor",
    "points_proj",
    "assists_proj",
    "rebounds_proj",
    "threes_proj",
    "threes_proj_legacy",
    "CATCH_SHOOT_FG3A",
    "CATCH_SHOOT_FG3_PCT",
    "PULL_UP_FG3A",
    "PULL_UP_FG3_PCT",
    "SPOTUP_PPP",
    "SPOTUP_POSS",
    "OFFSCREEN_PPP",
    "OFFSCREEN_POSS",
    "P_points_ge_20",
    "P_assists_ge_6",
    "P_rebounds_ge_8",
    "P_3pm_ge_3",
    "MC_P_points_ge_20",
    "MC_P_assists_ge_6",
    "MC_P_rebounds_ge_8",
    "MC_P_3pm_ge_3","P_points_ge_15",
    "P_points_ge_25",
    "P_points_ge_30",
    "P_assists_ge_4",
    "P_assists_ge_8",
    "P_assists_ge_10",
    "P_rebounds_ge_6",
    "P_rebounds_ge_10",
    "P_rebounds_ge_12",
    "P_3pm_ge_2",
    "P_3pm_ge_4",
    "P_3pm_ge_5",
    "P_3pm_ge_6",
    "P_3pm_ge_7",
    "MC_P_points_ge_15",
    "MC_P_points_ge_25",
    "MC_P_points_ge_30",
    "MC_P_assists_ge_4",
    "MC_P_assists_ge_8",
    "MC_P_assists_ge_10",
    "MC_P_rebounds_ge_6",
    "MC_P_rebounds_ge_10",
    "MC_P_rebounds_ge_12",
    "MC_P_3pm_ge_2",
    "MC_P_3pm_ge_4",
    "MC_P_3pm_ge_5",
    "MC_P_3pm_ge_6",
    "MC_P_3pm_ge_7",
    "P_poisson_points_ge_20",
    "P_poisson_assists_ge_6",
    "P_poisson_rebounds_ge_8",
    "P_poisson_3pm_ge_3",
    "pts_streak",
    "pts_streak_ratio",
    "ast_streak",
    "ast_streak_ratio",
    "reb_streak",
    "reb_streak_ratio",
    "fg3m_streak",
    "fg3m_streak_ratio",
]

ZONE_PLAYTYPE_BREAKDOWN_COLUMNS: Iterable[str] = [
    "PLAYER_ID",
    "PLAYER_NAME",
    "TEAM_ID",
    "TEAM_ABBREVIATION",
    # Zone shooting splits
    "RA_FGA",
    "RA_FG_PCT",
    "PAINT_NON_RA_FGA",
    "PAINT_NON_RA_FG_PCT",
    "MID_RANGE_FGA",
    "MID_RANGE_FG_PCT",
    "CORNER_3_FGA",
    "CORNER_3_FG_PCT",
    "ABOVE_BREAK_3_FGA",
    "ABOVE_BREAK_3_FG_PCT",
    # Computed zone and strategy factors
    "zone_points_raw",
    "team_strategy_factor",
    "dampened_strategy_factor",
]


def export_json(df: pd.DataFrame, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [col for col in DEFAULT_EXPORT_COLUMNS if col in df.columns]
    clean_df = _sanitize_for_export(df[columns])
    records = clean_df.to_dict(orient="records")
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    return output_path


def export_zone_playtype_breakdown(df: pd.DataFrame, output_path: Path = None) -> Path:
    """Export detailed zone shooting and play type strategy breakdown.
    
    Includes per-zone FG% and FGA, computed zone points, and strategy factors.
    This is kept separate from the main props file to avoid cluttering it with
    raw zone data while preserving detailed breakdown for analysis.
    """
    if output_path is None:
        output_path = Path("output/zone_playtype_breakdown.json")
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [col for col in ZONE_PLAYTYPE_BREAKDOWN_COLUMNS if col in df.columns]
    clean_df = _sanitize_for_export(df[columns])
    records = clean_df.to_dict(orient="records")
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    return output_path

def export_game_logs_with_streaks(
    df: pd.DataFrame,
    output_path: Path = None,
) -> Path:
    """
    Export recent game log aggregates with hot/cold streak flags.

    A player is HOT if their recent median is >= 1.15x their projection.
    A player is COLD if their recent median is <= 0.85x their projection.
    Otherwise NEUTRAL.
    """
    if output_path is None:
        output_path = Path("output/game_logs_recent.json")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if df.empty:
        with output_path.open("w") as f:
            json.dump([], f)
        return output_path

    out = df.copy()

    # Streak detection per stat category
    for stat, proj_col, median_col in [
        ("pts", "points_proj", "recent_pts_median"),
        ("ast", "assists_proj", "recent_ast_median"),
        ("reb", "rebounds_proj", "recent_reb_median"),
        ("fg3m", "threes_proj", "recent_fg3m_median"),
    ]:
        if proj_col in out.columns and median_col in out.columns:
            proj = pd.to_numeric(out[proj_col], errors="coerce").fillna(0)
            median = pd.to_numeric(out[median_col], errors="coerce").fillna(0)
            ratio = (median / proj.where(proj > 0, 1)).fillna(1.0)
            out[f"{stat}_streak"] = "NEUTRAL"
            out.loc[ratio >= 1.15, f"{stat}_streak"] = "HOT"
            out.loc[ratio <= 0.85, f"{stat}_streak"] = "COLD"
            out[f"{stat}_streak_ratio"] = ratio.round(3)

    keep = [c for c in [
        "PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "OPPONENT_ABBREVIATION",
        "points_proj", "assists_proj", "rebounds_proj", "threes_proj",
        "recent_games",
        "recent_min_avg", "recent_min_std",
        "recent_pts_avg", "recent_pts_median", "recent_pts_std",
        "recent_ast_avg", "recent_ast_median", "recent_ast_std",
        "recent_reb_avg", "recent_reb_median", "recent_reb_std",
        "recent_fg3m_avg", "recent_fg3m_median", "recent_fg3m_std",
        "pts_streak", "pts_streak_ratio",
        "ast_streak", "ast_streak_ratio",
        "reb_streak", "reb_streak_ratio",
        "fg3m_streak", "fg3m_streak_ratio",
    ] if c in out.columns]

    records = out[keep].to_dict(orient="records")
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    return output_path