from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PipelineConfig


def _safe_col(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce").fillna(default)
    return pd.Series(default, index=df.index)


def build_feature_table(
    player_stats: pd.DataFrame,
    tracking_stats: pd.DataFrame,
    reb_tracking: pd.DataFrame,
    team_defense: pd.DataFrame,
    matchups: pd.DataFrame,
    pbp_possessions: pd.DataFrame,
    config: PipelineConfig,
) -> pd.DataFrame:
    base = player_stats.copy()

    if "GS" in base.columns:
        base = base[pd.to_numeric(base["GS"], errors="coerce").fillna(0) >= config.min_games_started]
    elif "GP" in base.columns:
        base = base[pd.to_numeric(base["GP"], errors="coerce").fillna(0) >= config.min_games_started]

    for external in [tracking_stats, reb_tracking, pbp_possessions]:
        if external.empty:
            continue
        key_cols = [k for k in ["PLAYER_ID", "TEAM_ID"] if k in base.columns and k in external.columns]
        if not key_cols and "PLAYER_ID" in external.columns and "PLAYER_ID" in base.columns:
            key_cols = ["PLAYER_ID"]
        if key_cols:
            base = base.merge(external, on=key_cols, how="left", suffixes=("", "_x"))

    if not matchups.empty:
        base = base.merge(matchups[["TEAM_ABBREVIATION", "OPPONENT_ABBREVIATION", "GAME_ID", "GAME_DATETIME"]], on="TEAM_ABBREVIATION", how="inner")

    if not team_defense.empty and "OPPONENT_ABBREVIATION" in base.columns:
        team_def = team_defense.copy().rename(columns={"TEAM_ABBREVIATION": "OPPONENT_ABBREVIATION"})
        keep = [
            c
            for c in [
                "OPPONENT_ABBREVIATION",
                "OPP_AST",
                "OPP_FGA",
                "OPP_FG3A",
                "OPP_PTS",
                "DEF_RATING",
                "PACE",
            ]
            if c in team_def.columns
        ]
        if keep:
            base = base.merge(team_def[keep], on="OPPONENT_ABBREVIATION", how="left")

    base["projected_minutes"] = np.where(
        _safe_col(base, "MIN") > 0,
        _safe_col(base, "MIN") * 1.02,
        _safe_col(base, "PBP_MINUTES", 28),
    )
    base["possessions_per_game"] = np.where(
        _safe_col(base, "POSS_PER_GAME_EST") > 0,
        _safe_col(base, "POSS_PER_GAME_EST"),
        _safe_col(base, "PACE", 98) * (_safe_col(base, "projected_minutes") / 48),
    )
    base["usage_rate"] = _safe_col(base, "USG_PCT", 20) / 100
    base["touches"] = _safe_col(base, "TOUCHES", _safe_col(base, "PASSES_RECEIVED", 40))
    base["time_of_poss"] = _safe_col(base, "TIME_OF_POSS", 2.5)
    base["potential_assists"] = _safe_col(base, "POTENTIAL_AST", _safe_col(base, "PBP_POT_AST", _safe_col(base, "AST") * 1.7))
    base["rebound_chances"] = _safe_col(base, "REB_CHANCES", _safe_col(base, "PBP_REB_CHANCES", _safe_col(base, "REB") * 1.8))

    base["fga"] = _safe_col(base, "FGA")
    base["fg3a"] = _safe_col(base, "FG3A")
    base["fg_pct"] = _safe_col(base, "FG_PCT", 0.45)
    base["fg3_pct"] = _safe_col(base, "FG3_PCT", 0.35)

    # PnR proxy from touches and assist opportunities.
    base["pnr_proxy"] = (base["touches"] * 0.015) + (base["potential_assists"] * 0.08)

    # Opponent adjustment coefficients.
    base["opp_assist_factor"] = 1 + ((_safe_col(base, "OPP_AST", 24) - 24) / 100)
    base["opp_points_factor"] = 1 + ((_safe_col(base, "OPP_PTS", 112) - 112) / 150)
    base["opp_reb_factor"] = 1 + ((_safe_col(base, "OPP_FGA", 88) - 88) / 250)
    base["opp_3pa_factor"] = 1 + ((_safe_col(base, "OPP_FG3A", 34) - 34) / 180)

    return base
