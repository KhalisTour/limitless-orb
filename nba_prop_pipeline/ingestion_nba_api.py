"""
nba_api adapter — drop-in replacement for direct stats.nba.com calls.

The nba_api library has built-in retry logic, header management, and works
around residential IP blocking that affects raw requests calls. This module
exposes the same DataFrame-returning functions that ingestion.py provides,
so the rest of the pipeline doesn't need to change.

Usage in pipeline.py: replace
    from .ingestion import get_player_stats, get_team_defense_stats, ...
with
    from .ingestion_nba_api import get_player_stats, get_team_defense_stats, ...
"""
from __future__ import annotations

import logging
from typing import List

import pandas as pd

from .clients import CachedHTTPClient
from .config import PipelineConfig

logger = logging.getLogger(__name__)


def _nba_api_available() -> bool:
    try:
        import nba_api  # noqa: F401
        return True
    except ImportError:
        return False


def get_player_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    if not _nba_api_available():
        logger.error("nba_api not installed. Run: pip install nba_api")
        return pd.DataFrame()

    from nba_api.stats.endpoints import LeagueDashPlayerStats

    try:
        result = LeagueDashPlayerStats(
            season=config.season,
            season_type_all_star=config.season_type,
            per_mode_detailed="PerGame",
            measure_type_detailed_defense="Base",
            league_id_nullable="00",
            timeout=config.timeout_seconds,
        )
        return result.get_data_frames()[0]
    except Exception as exc:
        logger.warning("nba_api player stats failed: %s", exc)
        return pd.DataFrame()


def get_tracking_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    if not _nba_api_available():
        return pd.DataFrame()

    from nba_api.stats.endpoints import LeagueDashPtStats

    try:
        speed = LeagueDashPtStats(
            season=config.season,
            season_type_all_star=config.season_type,
            per_mode_simple="PerGame",
            pt_measure_type="SpeedDistance",
            player_or_team="Player",
            timeout=config.timeout_seconds,
        ).get_data_frames()[0]

        passing = LeagueDashPtStats(
            season=config.season,
            season_type_all_star=config.season_type,
            per_mode_simple="PerGame",
            pt_measure_type="Passing",
            player_or_team="Player",
            timeout=config.timeout_seconds,
        ).get_data_frames()[0]

        if not speed.empty and not passing.empty:
            keep = [
                "PLAYER_ID",
                "PLAYER_NAME",
                "TEAM_ID",
                "TEAM_ABBREVIATION",
                "TOUCHES",
                "TIME_OF_POSS",
                "PASSES_MADE",
                "PASSES_RECEIVED",
                "POTENTIAL_AST",
                "AST_ADJ",
                "AST_TO_PASS_PCT",
            ]
            available = [c for c in keep if c in passing.columns]
            join_keys = [c for c in ["PLAYER_ID", "TEAM_ID"] if c in passing.columns and c in speed.columns]
            return speed.merge(passing[available], on=join_keys, how="left")

        return passing if not passing.empty else speed

    except Exception as exc:
        logger.warning("nba_api tracking stats failed: %s", exc)
        return pd.DataFrame()


def get_rebounding_tracking_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    if not _nba_api_available():
        return pd.DataFrame()

    from nba_api.stats.endpoints import LeagueDashPtStats

    try:
        return LeagueDashPtStats(
            season=config.season,
            season_type_all_star=config.season_type,
            per_mode_simple="PerGame",
            pt_measure_type="Rebounding",
            player_or_team="Player",
            timeout=config.timeout_seconds,
        ).get_data_frames()[0]
    except Exception as exc:
        logger.warning("nba_api rebounding stats failed: %s", exc)
        return pd.DataFrame()

def get_team_defense_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    if not _nba_api_available():
        return pd.DataFrame()

    from nba_api.stats.endpoints import LeagueDashTeamStats

    try:
        defense = LeagueDashTeamStats(
            season=config.season,
            season_type_all_star=config.season_type,
            per_mode_detailed="PerGame",
            measure_type_detailed_defense="Defense",
            league_id_nullable="00",
            timeout=config.timeout_seconds,
        ).get_data_frames()[0]

        opp = LeagueDashTeamStats(
            season=config.season,
            season_type_all_star=config.season_type,
            per_mode_detailed="PerGame",
            measure_type_detailed_defense="Opponent",
            league_id_nullable="00",
            timeout=config.timeout_seconds,
        ).get_data_frames()[0]

        if defense.empty:
            return opp
        if opp.empty:
            return defense

        keep_opp = [c for c in ["TEAM_ID", "OPP_AST", "OPP_FGA", "OPP_FG3A", "OPP_PTS", "DEF_RATING", "PACE"] if c in opp.columns]
        return defense.merge(opp[keep_opp], on="TEAM_ID", how="left")
    except Exception as exc:
        logger.warning("nba_api team defense failed: %s", exc)
        return pd.DataFrame()


def get_positional_defense_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    if not _nba_api_available():
        return pd.DataFrame()

    from nba_api.stats.endpoints import LeagueDashTeamStats

    rows: List[pd.DataFrame] = []
    for position_group in ["Guard", "Forward", "Center"]:
        try:
            df = LeagueDashTeamStats(
                season=config.season,
                season_type_all_star=config.season_type,
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Opponent",
                league_id_nullable="00",
                player_position_abbreviation_nullable=position_group[0],  # G/F/C
                timeout=config.timeout_seconds,
            ).get_data_frames()[0]
            if not df.empty:
                df["POSITION_GROUP"] = position_group
                rows.append(df)
        except Exception as exc:
            logger.warning("nba_api positional defense (%s) failed: %s", position_group, exc)

    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def get_opponent_stats_for_slate(
    client: CachedHTTPClient,
    config: PipelineConfig,
    matchups_df: pd.DataFrame,
) -> pd.DataFrame:
    defense = get_team_defense_stats(client, config)
    if defense.empty or matchups_df.empty or "OPPONENT_ABBREVIATION" not in matchups_df.columns:
        return defense

    teams_on_slate = set(matchups_df["OPPONENT_ABBREVIATION"].dropna().unique().tolist())
    if "TEAM_ABBREVIATION" not in defense.columns:
        return defense
    return defense[defense["TEAM_ABBREVIATION"].isin(teams_on_slate)].copy()


def get_team_defensive_scheme_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    """Estimate team defensive scheme behavior. Falls back gracefully if Synergy unavailable."""
    defense = get_team_defense_stats(client, config)
    if defense.empty:
        return pd.DataFrame()

    out = pd.DataFrame()
    if "TEAM_ID" in defense.columns:
        out["TEAM_ID"] = defense["TEAM_ID"]
    if "TEAM_ABBREVIATION" in defense.columns:
        out["TEAM_ABBREVIATION"] = defense["TEAM_ABBREVIATION"]

    def _safe(col, default):
        if col in defense.columns:
            return pd.to_numeric(defense[col], errors="coerce").fillna(default)
        return pd.Series(default, index=defense.index)

    def_rating = _safe("DEF_RATING", 113)
    opp_3pa = _safe("OPP_FG3A", 34)
    opp_pts = _safe("OPP_PTS", 112)

    out["opp_trap_blitz_rate"] = (18 + (def_rating - def_rating.mean()) * 0.65).clip(lower=8, upper=35)
    out["opp_hedge_rate"] = (16 + (opp_3pa - opp_3pa.mean()) * 0.9).clip(lower=5, upper=35)
    out["opp_drop_rate"] = (42 - (opp_3pa - opp_3pa.mean()) * 0.8).clip(lower=18, upper=60)
    out["opp_switch_rate"] = (24 + (def_rating.mean() - def_rating) * 0.55).clip(lower=8, upper=45)
    out["opp_spot_up_efg"] = (0.53 + (opp_3pa - opp_3pa.mean()) * 0.002).clip(lower=0.46, upper=0.64)
    out["opp_rim_fg_pct"] = (0.64 + (opp_pts - opp_pts.mean()) * 0.0018).clip(lower=0.54, upper=0.73)

    return out


# These two don't go through stats.nba.com so they're imported from the original ingestion module
from .ingestion import (  # noqa: E402
    get_today_matchups,
    get_starting_lineups_scrape,
    get_pbpstats_possessions,
)
