"""
Projection Upgrade: Zone Shooting + Play Type Layer
=====================================================

Two new ingestion functions to add to `nba_prop_pipeline/ingestion_nba_api.py`,
plus feature merge logic for `features.py` and the projection override for
`projections.py`.

Design notes:
- Zone projection comes from LeagueDashPlayerShotLocations (one call, 5 zones
  after combining Left+Right corner into Corner 3, dropping Backcourt).
- Play type factor combines 4 Synergy endpoints (Transition, PRBallHandler,
  Isolation, Spotup) into a single DataFrame with a PLAY_TYPE_GROUP column.
- League medians for the 1.2x gate are computed from the data itself at
  merge time, not hardcoded.
- Dampening alpha = 0.85 because play type efficiency is a stronger predictor
  than zone volume distribution (league scouts erase optimal zones, but good
  scorers beat coverage via play type wrinkles).

Final projection formula:
    zone_points = sum(zone_FGA * PPA_by_zone)
    team_strategy_factor = weighted_avg(playtype_PPP / league_avg_PPP)
    dampened = 1 + alpha * (team_strategy_factor - 1)
    points_proj = zone_points * (projected_minutes / season_avg_min) * dampened * opp_points_factor
"""
from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

from .clients import CachedHTTPClient
from .config import PipelineConfig

logger = logging.getLogger(__name__)


# ============================================================================
# INGESTION FUNCTIONS (add to ingestion_nba_api.py)
# ============================================================================

def get_shot_locations_by_zone(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    """
    Pull per-zone shooting splits from LeagueDashPlayerShotLocations.

    The endpoint returns a MultiIndex DataFrame with columns like
    ('Restricted Area', 'FGA'). We flatten to underscored strings and
    combine Left Corner 3 + Right Corner 3 into a single Corner 3 bucket.
    Backcourt is dropped (noise, near-zero volume).

    Returns columns like: PLAYER_ID, TEAM_ID, RA_FGA, RA_FG_PCT,
    PAINT_NON_RA_FGA, PAINT_NON_RA_FG_PCT, MID_RANGE_FGA, MID_RANGE_FG_PCT,
    CORNER_3_FGA, CORNER_3_FG_PCT, ABOVE_THE_BREAK_3_FGA, ABOVE_THE_BREAK_3_FG_PCT
    """
    try:
        from nba_api.stats.endpoints import LeagueDashPlayerShotLocations
    except ImportError:
        logger.error("nba_api not installed")
        return pd.DataFrame()

    try:
        result = LeagueDashPlayerShotLocations(
            season=config.season,
            season_type_all_star=config.season_type,
            per_mode_detailed="PerGame",
            distance_range="By Zone",
            league_id_nullable="00",
            timeout=config.timeout_seconds,
        )
        df = result.get_data_frames()[0]
    except Exception as exc:
        logger.warning("Shot locations pull failed: %s", exc)
        return pd.DataFrame()

    if df.empty:
        return df

    # Flatten MultiIndex columns like ('Restricted Area', 'FGA') -> 'RA_FGA'
    zone_abbrev = {
        "Restricted Area": "RA",
        "In The Paint (Non-RA)": "PAINT_NON_RA",
        "Mid-Range": "MID_RANGE",
        "Left Corner 3": "LEFT_CORNER_3",
        "Right Corner 3": "RIGHT_CORNER_3",
        "Above the Break 3": "ABOVE_BREAK_3",
        "Backcourt": "BACKCOURT",
        "Corner 3": "CORNER_3_AGG",  # pre-aggregated, we'll build our own from L+R
    }

    flat_cols = []
    for col in df.columns:
        if isinstance(col, tuple):
            zone, stat = col[0], col[1] if len(col) > 1 else ""
            zone = str(zone).strip()
            stat = str(stat).strip()
            if zone == "":
                flat_cols.append(stat)
            else:
                prefix = zone_abbrev.get(zone, zone.upper().replace(" ", "_"))
                flat_cols.append(f"{prefix}_{stat}")
        else:
            flat_cols.append(str(col))
    df.columns = flat_cols

    # Combine Left + Right Corner 3 into a single Corner 3 bucket (how defenses think about it)
    if "LEFT_CORNER_3_FGA" in df.columns and "RIGHT_CORNER_3_FGA" in df.columns:
        df["CORNER_3_FGA"] = df["LEFT_CORNER_3_FGA"].fillna(0) + df["RIGHT_CORNER_3_FGA"].fillna(0)
        df["CORNER_3_FGM"] = df["LEFT_CORNER_3_FGM"].fillna(0) + df["RIGHT_CORNER_3_FGM"].fillna(0)
        df["CORNER_3_FG_PCT"] = np.where(
            df["CORNER_3_FGA"] > 0,
            df["CORNER_3_FGM"] / df["CORNER_3_FGA"],
            0.0,
        )

    # Keep only the columns we need for the projection
    keep_cols = ["PLAYER_ID", "TEAM_ID", "PLAYER_NAME"]
    for prefix in ["RA", "PAINT_NON_RA", "MID_RANGE", "CORNER_3", "ABOVE_BREAK_3"]:
        for stat in ["FGA", "FG_PCT"]:
            col = f"{prefix}_{stat}"
            if col in df.columns:
                keep_cols.append(col)

    available = [c for c in keep_cols if c in df.columns]
    return df[available].copy()


def get_synergy_play_types(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    """
    Pull 4 Synergy play types (Transition, PRBallHandler, Isolation, Spotup)
    and return a combined DataFrame with a PLAY_TYPE_GROUP column.

    One row per player per play type they participate in. Used by features.py
    to compute the team strategy factor.
    """
    try:
        from nba_api.stats.endpoints import SynergyPlayTypes
    except ImportError:
        logger.error("nba_api not installed")
        return pd.DataFrame()

    play_types = ["Transition", "PRBallHandler", "Isolation", "Spotup"]
    frames = []

    for pt in play_types:
        try:
            result = SynergyPlayTypes(
                league_id="00",
                per_mode_simple="PerGame",
                play_type_nullable=pt,
                player_or_team_abbreviation="P",
                season=config.season,
                season_type_all_star=config.season_type,
                type_grouping_nullable="offensive",
                timeout=config.timeout_seconds,
            )
            df = result.get_data_frames()[0]
            if not df.empty:
                df["PLAY_TYPE_GROUP"] = pt
                frames.append(df)
        except Exception as exc:
            logger.warning("Synergy play type %s failed: %s", pt, exc)
            continue

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)

    # Keep only the columns we need
    keep = [c for c in [
        "PLAYER_ID", "TEAM_ID", "PLAYER_NAME", "PLAY_TYPE_GROUP",
        "GP", "POSS", "PPP", "FG_PCT", "EFG_PCT", "PTS",
    ] if c in combined.columns]
    return combined[keep].copy()


# ============================================================================
# FEATURE MERGE (add to features.py)
# ============================================================================

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
    else:
        out["team_strategy_factor"] = 1.0

    # Apply dampening alpha
    alpha = getattr(config, "points_projection_alpha", 0.85)
    out["dampened_strategy_factor"] = 1 + alpha * (out["team_strategy_factor"] - 1)

    return out


# ============================================================================
# PROJECTION OVERRIDE (replace points_proj logic in projections.py)
# ============================================================================

def compute_zone_based_points_projection(df: pd.DataFrame, config: PipelineConfig) -> pd.Series:
    """
    Final points projection using zone decomposition + play type factor + opponent.

        points_proj = zone_points_raw
                      * (projected_minutes / avg_minutes)
                      * dampened_strategy_factor
                      * opp_points_factor

    zone_points_raw is already at the player's season-average minutes pace.
    We scale it to the projected minutes for tonight's game, then apply the
    strategy and opponent multipliers.
    """
    def _safe(col: str, default: float = 0.0) -> pd.Series:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").fillna(default)
        return pd.Series(default, index=df.index)

    zone_points = _safe("zone_points_raw")
    proj_min = _safe("projected_minutes", 30)
    season_min = _safe("MIN", 30)
    # Avoid divide by zero
    season_min = season_min.where(season_min > 0, 30)
    minutes_scale = (proj_min / season_min).clip(lower=0.5, upper=1.5)

    strategy = _safe("dampened_strategy_factor", 1.0)
    opp_factor = _safe("opp_points_factor", 1.0)

    return zone_points * minutes_scale * strategy * opp_factor
