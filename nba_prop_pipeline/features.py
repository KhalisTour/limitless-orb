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


def add_zone_and_playtype_features(
    base: pd.DataFrame,
    zone_df: pd.DataFrame,
    playtype_df: pd.DataFrame,
    config: PipelineConfig,
) -> pd.DataFrame:
    """
    Merge zone shooting splits and play type efficiency into the feature table.

    Computes:
        - zone_points_raw: sum of (zone_FGA * points_per_attempt) per zone
        - team_strategy_factor: weighted avg of play type efficiency vs league
          median, gated by 1.2x median POSS threshold per play type
        - dampened_strategy_factor: 1 + alpha * (factor - 1) where
          alpha = config.points_projection_alpha (default 0.85)
    """
    out = base.copy()

    # Verify columns required by the new scoring engine projection are present
    required_cols = ["FTA", "FT_PCT", "TS_PCT", "usage_rate"]
    missing = [c for c in required_cols if c not in out.columns]
    if missing:
        logger.warning(
            "Missing columns required by scoring engine projection: %s. "
            "Free throw points and star multiplier may use defaults.",
            missing,
        )

    # --- Zone shooting merge ---
    if zone_df is not None and not zone_df.empty:
        merge_keys = [k for k in ["PLAYER_ID", "TEAM_ID"] if k in out.columns and k in zone_df.columns]
        if not merge_keys and "PLAYER_ID" in zone_df.columns and "PLAYER_ID" in out.columns:
            merge_keys = ["PLAYER_ID"]
        if merge_keys:
            # Drop duplicate name column from zone_df before merge
            zone_merge = zone_df.drop(columns=[c for c in ["PLAYER_NAME"] if c in zone_df.columns])
            out = out.merge(zone_merge, on=merge_keys, how="left", suffixes=("", "_zone"))

    # Compute zone-based points (points per attempt by zone)
    # PPA = 2 * FG% for 2pt zones, 3 * FG% for 3pt zones
    def _safe(col: str, default: float = 0.0) -> pd.Series:
        if col in out.columns:
            return pd.to_numeric(out[col], errors="coerce").fillna(default)
        return pd.Series(default, index=out.index)

    ra_points = _safe("RA_FGA") * _safe("RA_FG_PCT", 0.6) * 2
    paint_points = _safe("PAINT_NON_RA_FGA") * _safe("PAINT_NON_RA_FG_PCT", 0.42) * 2
    mid_points = _safe("MID_RANGE_FGA") * _safe("MID_RANGE_FG_PCT", 0.42) * 2
    corner3_points = _safe("CORNER_3_FGA") * _safe("CORNER_3_FG_PCT", 0.38) * 3
    atb3_points = _safe("ABOVE_BREAK_3_FGA") * _safe("ABOVE_BREAK_3_FG_PCT", 0.35) * 3

    out["zone_points_raw"] = ra_points + paint_points + mid_points + corner3_points + atb3_points

    # --- Play type merge and team strategy factor ---
    if playtype_df is not None and not playtype_df.empty and "PLAY_TYPE_GROUP" in playtype_df.columns:
        # Compute league medians and thresholds per play type
        league_medians = {}
        league_avg_ppp = {}
        thresh_multiplier = getattr(config, "playtype_poss_threshold_multiplier", 1.2)

        for pt in playtype_df["PLAY_TYPE_GROUP"].unique():
            subset = playtype_df[
                (playtype_df["PLAY_TYPE_GROUP"] == pt)
                & (pd.to_numeric(playtype_df["POSS"], errors="coerce") > 0)
            ]
            if len(subset):
                median_poss = pd.to_numeric(subset["POSS"], errors="coerce").median()
                avg_ppp = pd.to_numeric(subset["PPP"], errors="coerce").mean()
                league_medians[pt] = median_poss * thresh_multiplier
                league_avg_ppp[pt] = avg_ppp

        # For each player, compute team_strategy_factor from their qualifying play types
        def compute_player_factor(player_id, team_id):
            player_rows = playtype_df[
                (playtype_df["PLAYER_ID"] == player_id)
                & (playtype_df["TEAM_ID"] == team_id)
            ]
            if len(player_rows) == 0:
                return 1.0  # no play type data, neutral factor

            qualifying = []
            for _, row in player_rows.iterrows():
                pt = row["PLAY_TYPE_GROUP"]
                poss = pd.to_numeric(row.get("POSS", 0), errors="coerce")
                ppp = pd.to_numeric(row.get("PPP", 0), errors="coerce")
                threshold = league_medians.get(pt, float("inf"))
                league_ppp = league_avg_ppp.get(pt, 1.0)

                if pd.notna(poss) and pd.notna(ppp) and poss >= threshold and league_ppp > 0:
                    efficiency_ratio = ppp / league_ppp
                    qualifying.append({"poss": poss, "ratio": efficiency_ratio})

            if not qualifying:
                return 1.0  # no qualifying play types, neutral factor

            total_poss = sum(q["poss"] for q in qualifying)
            if total_poss == 0:
                return 1.0

            weighted = sum((q["poss"] / total_poss) * q["ratio"] for q in qualifying)
            return float(weighted)

        factors = []
        for _, row in out.iterrows():
            pid = row.get("PLAYER_ID")
            tid = row.get("TEAM_ID")
            if pd.isna(pid) or pd.isna(tid):
                factors.append(1.0)
            else:
                factors.append(compute_player_factor(int(pid), int(tid)))
        out["team_strategy_factor"] = factors
        # Pivot individual play type PPP onto the feature table for downstream use
        if "PPP" in playtype_df.columns:
            for pt in playtype_df["PLAY_TYPE_GROUP"].unique():
                pt_subset = playtype_df[playtype_df["PLAY_TYPE_GROUP"] == pt][["PLAYER_ID", "TEAM_ID", "PPP", "POSS"]].copy()
                pt_label = pt.upper().replace(" ", "")  # e.g. "Spotup" -> "SPOTUP"
                pt_subset = pt_subset.rename(columns={
                    "PPP": f"{pt_label}_PPP",
                    "POSS": f"{pt_label}_POSS",
                })
                merge_keys = [k for k in ["PLAYER_ID", "TEAM_ID"] if k in out.columns and k in pt_subset.columns]
                if merge_keys:
                    out = out.merge(pt_subset, on=merge_keys, how="left", suffixes=("", f"_{pt_label}"))
    else:
        out["team_strategy_factor"] = 1.0

    # Apply dampening alpha
    alpha = getattr(config, "points_projection_alpha", 0.85)
    out["dampened_strategy_factor"] = 1 + alpha * (out["team_strategy_factor"] - 1)

    return out


def build_feature_table(
    player_stats: pd.DataFrame,
    tracking_stats: pd.DataFrame,
    reb_tracking: pd.DataFrame,
    team_defense: pd.DataFrame,
    team_scheme_stats: pd.DataFrame,
    positional_defense_stats: pd.DataFrame | None,
    matchups: pd.DataFrame,
    pbp_possessions: pd.DataFrame,
    player_advanced: pd.DataFrame = None,
    zone_shot_locations: pd.DataFrame | None = None,
    playtype_stats: pd.DataFrame | None = None,
    catch_and_shoot_stats: pd.DataFrame | None = None,
    pullup_shot_stats: pd.DataFrame | None = None,
    config: PipelineConfig = None,
) -> pd.DataFrame:
    base = player_stats.copy()

    # Filter to starter-caliber players.
    # Use GS/GP start rate when GS column exists, otherwise fall back to GP + minutes threshold.
    gp_col = pd.to_numeric(base["GP"], errors="coerce").fillna(0) if "GP" in base.columns else pd.Series(0, index=base.index)
    min_gp = max(15, config.min_games_started // 2)

    if "GS" in base.columns:
        gs_col = pd.to_numeric(base["GS"], errors="coerce").fillna(0)
        start_rate = gs_col / gp_col.where(gp_col > 0, 1)
        starter_mask = (start_rate >= 0.7) & (gp_col >= min_gp)
        logger.info("Starter filter (GS/GP mode): keeping %d of %d players (GS/GP >= 0.7, GP >= %d)", starter_mask.sum(), len(base), min_gp)
    elif "MIN" in base.columns:
        mpg = pd.to_numeric(base["MIN"], errors="coerce").fillna(0)
        starter_mask = (gp_col >= min_gp) & (mpg >= 20)
        logger.info("Starter filter (GP+MIN mode): keeping %d of %d players (GP >= %d, MPG >= 20)", starter_mask.sum(), len(base), min_gp)
    else:
        starter_mask = gp_col >= min_gp
        logger.info("Starter filter (GP only mode): keeping %d of %d players (GP >= %d)", starter_mask.sum(), len(base), min_gp)

    base = base[starter_mask].copy()

    for suffix, external in zip(["_tracking", "_reb", "_pbp"], [tracking_stats, reb_tracking, pbp_possessions]):
        if external.empty:
            continue
        key_cols = [k for k in ["PLAYER_ID", "TEAM_ID"] if k in base.columns and k in external.columns]
        if not key_cols and "PLAYER_ID" in external.columns and "PLAYER_ID" in base.columns:
            key_cols = ["PLAYER_ID"]
        if key_cols:
            base = base.merge(external, on=key_cols, how="left", suffixes=("", suffix))

    if player_advanced is not None and not player_advanced.empty:
        adv_keep = [c for c in ["PLAYER_ID", "TEAM_ID", "USG_PCT", "TS_PCT", "EFG_PCT",
                                "AST_PCT", "OREB_PCT", "DREB_PCT", "TOV_PCT",
                                "NET_RATING", "OFF_RATING"] if c in player_advanced.columns]
        if "PLAYER_ID" in adv_keep:
            key_cols = [k for k in ["PLAYER_ID", "TEAM_ID"] if k in base.columns and k in player_advanced.columns]
            base = base.merge(player_advanced[adv_keep], on=key_cols, how="left", suffixes=("", "_adv"))
# Merge catch-and-shoot tracking stats
    if catch_and_shoot_stats is not None and not catch_and_shoot_stats.empty:
        cs_keep = [c for c in [
            "PLAYER_ID", "TEAM_ID",
            "CATCH_SHOOT_FGM", "CATCH_SHOOT_FGA", "CATCH_SHOOT_FG_PCT",
            "CATCH_SHOOT_PTS", "CATCH_SHOOT_FG3M", "CATCH_SHOOT_FG3A", "CATCH_SHOOT_FG3_PCT",
            "CATCH_SHOOT_EFG_PCT",
        ] if c in catch_and_shoot_stats.columns]
        if "PLAYER_ID" in cs_keep:
            cs_keys = [k for k in ["PLAYER_ID", "TEAM_ID"] if k in base.columns and k in catch_and_shoot_stats.columns]
            base = base.merge(catch_and_shoot_stats[cs_keep], on=cs_keys, how="left", suffixes=("", "_cs"))

    # Merge pull-up shot tracking stats
    if pullup_shot_stats is not None and not pullup_shot_stats.empty:
        pu_keep = [c for c in [
            "PLAYER_ID", "TEAM_ID",
            "PULL_UP_FGM", "PULL_UP_FGA", "PULL_UP_FG_PCT",
            "PULL_UP_PTS", "PULL_UP_FG3M", "PULL_UP_FG3A", "PULL_UP_FG3_PCT",
            "PULL_UP_EFG_PCT",
        ] if c in pullup_shot_stats.columns]
        if "PLAYER_ID" in pu_keep:
            pu_keys = [k for k in ["PLAYER_ID", "TEAM_ID"] if k in base.columns and k in pullup_shot_stats.columns]
            base = base.merge(pullup_shot_stats[pu_keep], on=pu_keys, how="left", suffixes=("", "_pu"))
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

    # Team defense merge ALWAYS runs, regardless of matchup validity.
    if not team_defense.empty and "OPPONENT_ABBREVIATION" in base.columns:
        team_def = team_defense.copy()
        
        # If team_def doesn't have TEAM_ABBREVIATION but base does, create a mapping from TEAM_ID
        if "TEAM_ABBREVIATION" not in team_def.columns and "TEAM_ID" in team_def.columns and "TEAM_ID" in base.columns:
            team_abbrev_map = base[["TEAM_ID", "TEAM_ABBREVIATION"]].drop_duplicates().dropna()
            if not team_abbrev_map.empty:
                team_def = team_def.merge(team_abbrev_map, on="TEAM_ID", how="left")
        
        if "TEAM_ABBREVIATION" in team_def.columns:
            team_def = team_def.rename(columns={"TEAM_ABBREVIATION": "OPPONENT_ABBREVIATION"})

        if "OPPONENT_ABBREVIATION" in team_def.columns:
            keep = [
                c
                for c in [
                    "OPPONENT_ABBREVIATION",
                    "OPP_AST",
                    "OPP_FGA",
                    "OPP_FG3A"
                    "OPP_FG3_PCT",
                    "OPP_PTS",
                    "DEF_RATING",
                    "PACE",
                ]
                if c in team_def.columns
            ]
            if "OPPONENT_ABBREVIATION" in keep:
                base = base.merge(team_def[keep], on="OPPONENT_ABBREVIATION", how="left", suffixes=("", "_teamdef"))

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
    usg_raw = _safe_col(base, "USG_PCT", 20)
    base["usage_rate"] = np.where(usg_raw > 1.0, usg_raw / 100, usg_raw)
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

    if zone_shot_locations is not None or playtype_stats is not None:
        base = add_zone_and_playtype_features(
            base,
            zone_shot_locations if zone_shot_locations is not None else pd.DataFrame(),
            playtype_stats if playtype_stats is not None else pd.DataFrame(),
            config,
        )

    return base
