from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import PipelineConfig

logger = logging.getLogger(__name__)


def _safe_col(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce").fillna(default)
    return pd.Series(default, index=df.index)


def build_feature_table(
    player_stats: pd.DataFrame,
    tracking_stats: pd.DataFrame,
    reb_tracking: pd.DataFrame,
    team_defense: pd.DataFrame,
    team_scheme_stats: pd.DataFrame,
    positional_defense_stats: pd.DataFrame | None,
    matchups: pd.DataFrame,
    pbp_possessions: pd.DataFrame,
    config: PipelineConfig,
) -> pd.DataFrame:
    base = player_stats.copy()

    if "GS" in base.columns:
        base = base[pd.to_numeric(base["GS"], errors="coerce").fillna(0) >= config.min_games_started]
    elif "GP" in base.columns:
        base = base[pd.to_numeric(base["GP"], errors="coerce").fillna(0) >= config.min_games_started]

    for suffix, external in zip(["_tracking", "_reb", "_pbp"], [tracking_stats, reb_tracking, pbp_possessions]):
        if external.empty:
            continue
        key_cols = [k for k in ["PLAYER_ID", "TEAM_ID"] if k in base.columns and k in external.columns]
        if not key_cols and "PLAYER_ID" in external.columns and "PLAYER_ID" in base.columns:
            key_cols = ["PLAYER_ID"]
        if key_cols:
            base = base.merge(external, on=key_cols, how="left", suffixes=("", suffix))

    valid_matchups = (
        not matchups.empty
        and "TEAM_ABBREVIATION" in base.columns
        and {"TEAM_ABBREVIATION", "OPPONENT_ABBREVIATION", "GAME_ID", "GAME_DATETIME"}.issubset(matchups.columns)
    )
    if valid_matchups:
        base = base.merge(
            matchups[["TEAM_ABBREVIATION", "OPPONENT_ABBREVIATION", "GAME_ID", "GAME_DATETIME"]],
            on="TEAM_ABBREVIATION",
            how="inner",
        )
    else:
        base["OPPONENT_ABBREVIATION"] = pd.NA
        base["GAME_ID"] = pd.NA
        base["GAME_DATETIME"] = pd.NA

        if not team_defense.empty and "OPPONENT_ABBREVIATION" in base.columns:
            team_def = team_defense.copy()
            if "TEAM_ABBREVIATION" in team_def.columns:
                team_def = team_def.rename(columns={"TEAM_ABBREVIATION": "OPPONENT_ABBREVIATION"})

            if "OPPONENT_ABBREVIATION" in team_def.columns:
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
                if "OPPONENT_ABBREVIATION" in keep:
                    base = base.merge(team_def[keep], on="OPPONENT_ABBREVIATION", how="left")

    if positional_defense_stats is not None and not positional_defense_stats.empty and "OPPONENT_ABBREVIATION" in base.columns:
        def _position_group(raw: str) -> str:
            pos = str(raw or "").upper()
            if "C" in pos and "F" not in pos:
                return "Center"
            if pos in {"G", "G-F", "F-G"} or pos.startswith("G"):
                return "Guard"
            if "F" in pos:
                return "Forward"
            return "Guard"

        if "PLAYER_POSITION" in base.columns:
            base["POSITION_GROUP"] = base["PLAYER_POSITION"].map(_position_group)
        elif "POSITION" in base.columns:
            base["POSITION_GROUP"] = base["POSITION"].map(_position_group)
        else:
            base["POSITION_GROUP"] = "Guard"

        pos = positional_defense_stats.copy().rename(columns={"TEAM_ABBREVIATION": "OPPONENT_ABBREVIATION"})
        keep_pos = [c for c in ["OPPONENT_ABBREVIATION", "POSITION_GROUP", "OPP_AST", "OPP_FGA", "OPP_FG3A", "OPP_PTS"] if c in pos.columns]
        if {"OPPONENT_ABBREVIATION", "POSITION_GROUP"}.issubset(keep_pos):
            pos = pos[keep_pos].rename(
                columns={
                    "OPP_AST": "POS_OPP_AST",
                    "OPP_FGA": "POS_OPP_FGA",
                    "OPP_FG3A": "POS_OPP_FG3A",
                    "OPP_PTS": "POS_OPP_PTS",
                }
            )
            base = base.merge(pos, on=["OPPONENT_ABBREVIATION", "POSITION_GROUP"], how="left")

    if not team_scheme_stats.empty and "OPPONENT_ABBREVIATION" in base.columns:
        scheme = team_scheme_stats.copy()
        if "TEAM_ABBREVIATION" in scheme.columns:
            scheme = scheme.rename(columns={"TEAM_ABBREVIATION": "OPPONENT_ABBREVIATION"})

        if "OPPONENT_ABBREVIATION" in scheme.columns:
            keep_scheme = [
                c
                for c in [
                    "OPPONENT_ABBREVIATION",
                    "opp_trap_blitz_rate",
                    "opp_hedge_rate",
                    "opp_drop_rate",
                    "opp_switch_rate",
                    "opp_spot_up_efg",
                    "opp_rim_fg_pct",
                ]
                if c in scheme.columns
            ]
            if "OPPONENT_ABBREVIATION" in keep_scheme:
                base = base.merge(scheme[keep_scheme], on="OPPONENT_ABBREVIATION", how="left")

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
    opp_ast_used = _safe_col(base, "POS_OPP_AST", 0).where(_safe_col(base, "POS_OPP_AST", 0) > 0, _safe_col(base, "OPP_AST", 24))
    opp_pts_used = _safe_col(base, "POS_OPP_PTS", 0).where(_safe_col(base, "POS_OPP_PTS", 0) > 0, _safe_col(base, "OPP_PTS", 112))
    opp_fga_used = _safe_col(base, "POS_OPP_FGA", 0).where(_safe_col(base, "POS_OPP_FGA", 0) > 0, _safe_col(base, "OPP_FGA", 88))
    opp_fg3a_used = _safe_col(base, "POS_OPP_FG3A", 0).where(_safe_col(base, "POS_OPP_FG3A", 0) > 0, _safe_col(base, "OPP_FG3A", 34))
    base["opp_assist_factor"] = 1 + ((opp_ast_used - 24) / 100)
    base["opp_points_factor"] = 1 + ((opp_pts_used - 112) / 150)
    base["opp_reb_factor"] = 1 + ((opp_fga_used - 88) / 250)
    base["opp_3pa_factor"] = 1 + ((opp_fg3a_used - 34) / 180)

    if "OPPONENT_ABBREVIATION" in base.columns and not team_defense.empty and "TEAM_ABBREVIATION" in team_defense.columns:
        missing_mask = ~base["OPPONENT_ABBREVIATION"].isin(team_defense["TEAM_ABBREVIATION"].dropna().unique())
        missing_teams = sorted(base.loc[missing_mask, "OPPONENT_ABBREVIATION"].dropna().unique().tolist())
        if missing_teams:
            logger.warning("Missing opponent defense stats for teams on slate: %s", ", ".join(missing_teams))

    # Defensive scheme columns (fallback to league averages so pipeline remains resilient).
    base["opp_trap_blitz_rate"] = _safe_col(base, "opp_trap_blitz_rate", config.league_avg_trap_blitz_rate)
    base["opp_hedge_rate"] = _safe_col(base, "opp_hedge_rate", config.league_avg_hedge_rate)
    base["opp_drop_rate"] = _safe_col(base, "opp_drop_rate", 40.0)
    base["opp_switch_rate"] = _safe_col(base, "opp_switch_rate", 24.0)
    base["opp_spot_up_efg"] = _safe_col(base, "opp_spot_up_efg", 0.54)
    base["opp_rim_fg_pct"] = _safe_col(base, "opp_rim_fg_pct", 0.64)

    # Stretch indicator and minute risk components consumed downstream by the projection layer.
    base["high5_stretch_indicator"] = (
        (_safe_col(base, "FG3A", 0) >= 5.0)
        & (_safe_col(base, "projected_minutes", 0) >= 24)
    ).astype(float)
    blowout_proxy = (_safe_col(base, "OPP_PTS", 112) - 112).abs()
    base["blowout_risk_penalty"] = ((blowout_proxy - 6) / 100).clip(lower=0, upper=0.06)
    base["foul_risk_penalty"] = (
        (_safe_col(base, "opp_trap_blitz_rate", config.league_avg_trap_blitz_rate) - config.league_avg_trap_blitz_rate) / 400
    ).clip(lower=0, upper=0.04)

    return base
