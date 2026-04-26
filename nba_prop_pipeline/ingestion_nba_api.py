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

import numpy as np
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


def get_player_advanced_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    if not _nba_api_available():
        logger.error("nba_api not installed. Run: pip install nba_api")
        return pd.DataFrame()

    from nba_api.stats.endpoints import LeagueDashPlayerStats

    try:
        result = LeagueDashPlayerStats(
            season=config.season,
            season_type_all_star=config.season_type,
            per_mode_detailed="PerGame",
            measure_type_detailed_defense="Advanced",
            league_id_nullable="00",
            timeout=config.timeout_seconds,
        )
        return result.get_data_frames()[0]
    except Exception as exc:
        logger.warning("nba_api advanced player stats failed: %s", exc)
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
        "Corner 3": "CORNER_3_AGG",
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

    play_types = ["Transition", "PRBallHandler", "Isolation", "Spotup", "OffScreen"]
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
def get_catch_and_shoot_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    """Pull per-player catch-and-shoot tracking stats (3PA, 3P%, FGA, FG%)."""
    if not _nba_api_available():
        return pd.DataFrame()

    from nba_api.stats.endpoints import LeagueDashPtStats

    try:
        result = LeagueDashPtStats(
            season=config.season,
            season_type_all_star=config.season_type,
            per_mode_simple="PerGame",
            pt_measure_type="CatchShoot",
            player_or_team="Player",
            timeout=config.timeout_seconds,
        ).get_data_frames()[0]
        if not result.empty:
            logger.info("Catch-and-shoot stats: %d players", len(result))
        return result
    except Exception as exc:
        logger.warning("Catch-and-shoot stats pull failed: %s", exc)
        return pd.DataFrame()


def get_pullup_shot_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    """Pull per-player pull-up shooting tracking stats (3PA, 3P%, FGA, FG%)."""
    if not _nba_api_available():
        return pd.DataFrame()

    from nba_api.stats.endpoints import LeagueDashPtStats

    try:
        result = LeagueDashPtStats(
            season=config.season,
            season_type_all_star=config.season_type,
            per_mode_simple="PerGame",
            pt_measure_type="PullUpShot",
            player_or_team="Player",
            timeout=config.timeout_seconds,
        ).get_data_frames()[0]
        if not result.empty:
            logger.info("Pull-up shot stats: %d players", len(result))
        return result
    except Exception as exc:
        logger.warning("Pull-up shot stats pull failed: %s", exc)
        return pd.DataFrame()


def get_player_game_logs(client: CachedHTTPClient, config: PipelineConfig, last_n_games: int = 5) -> pd.DataFrame:
    """
    Pull recent game logs for all players from the PBP Stats API.

    Returns per-player rolling stats from the last N games:
    - Recent minutes (rolling average and std dev)
    - Recent FGA, FG3A, PTS, AST, REB per game
    - Games played in last 7/14 days (recency signal)

    This data powers:
    1. Real minutes projection (rolling avg instead of season avg × 1.02)
    2. Recent form weighting for counting stats
    3. Empirical variance for negative binomial dispersion
    """
    if not _nba_api_available():
        return pd.DataFrame()

    from nba_api.stats.endpoints import PlayerGameLogs

    try:
        result = PlayerGameLogs(
            season_nullable=config.season,
            season_type_nullable=config.season_type,
            last_n_games_nullable=last_n_games,
            timeout=config.timeout_seconds,
        )
        df = result.get_data_frames()[0]
        if df.empty:
            logger.warning("PlayerGameLogs returned empty")
            return pd.DataFrame()

        logger.info("Player game logs: %d rows across %d games", len(df), last_n_games)

        agg = df.groupby("PLAYER_ID").agg(
            recent_games=("PLAYER_ID", "count"),
            recent_min_avg=("MIN", "mean"),
            recent_min_std=("MIN", "std"),
            recent_min_max=("MIN", "max"),
            recent_min_min=("MIN", "min"),
            recent_pts_avg=("PTS", "mean"),
            recent_ast_avg=("AST", "mean"),
            recent_reb_avg=("REB", "mean"),
            recent_fga_avg=("FGA", "mean"),
            recent_fg3a_avg=("FG3A", "mean"),
            recent_fg3m_avg=("FG3M", "mean"),
            recent_fta_avg=("FTA", "mean"),
            recent_ftm_avg=("FTM", "mean"),
            recent_pts_std=("PTS", "std"),
            recent_ast_std=("AST", "std"),
            recent_reb_std=("REB", "std"),
            recent_fg3m_std=("FG3M", "std"),
        ).reset_index()

        for col in agg.columns:
            if col.endswith("_std"):
                agg[col] = agg[col].fillna(0)

        return agg

    except Exception as exc:
        logger.warning("Player game logs pull failed: %s", exc)
        return pd.DataFrame()


def get_pbpstats_possessions_direct(config: PipelineConfig) -> pd.DataFrame:
    """
    Pull player possession data directly from PBP Stats API.
    Uses get-game-stats endpoint with Type=Player.
    """
    import requests as req

    GAMES_URL = "https://api.pbpstats.com/get-games/nba"
    GAME_STATS_URL = "https://api.pbpstats.com/get-game-stats"

    headers = {
        "User-Agent": config.user_agent,
        "Accept": "application/json",
    }

    try:
        resp = req.get(
            GAMES_URL,
            params={"Season": config.season, "SeasonType": "Regular Season"},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        games = (data.get("results") or data.get("games") or [])[-15:]
    except Exception as exc:
        logger.warning("PBP Stats games list failed: %s", exc)
        return pd.DataFrame(columns=["PLAYER_ID", "PBP_MINUTES", "POSS_PER_GAME_EST", "PBP_REB_CHANCES", "PBP_POT_AST"])

    records = []
    for game in games:
        game_id = game.get("GameId") or game.get("game_id")
        if not game_id:
            continue
        try:
            detail_resp = req.get(
                GAME_STATS_URL,
                params={"Type": "Player", "GameId": game_id},
                headers=headers,
                timeout=30,
            )
            detail_resp.raise_for_status()
            details = detail_resp.json()
        except Exception as exc:
            logger.warning("PBP Stats game %s failed: %s", game_id, exc)
            continue

        # Players are under stats -> Home/Away -> period keys ("1","2",..."FullGame")
        # Use "FullGame" if available, otherwise combine from period "1"
        for side in ["Home", "Away"]:
            side_data = details.get("stats", {}).get(side, {})
            # Prefer FullGame, fall back to period "1"
            players = side_data.get("FullGame") or side_data.get("1") or []
            for p in players:
                if p.get("Name") == "Team":
                    continue
                entity_id = p.get("EntityId")
                if not entity_id or entity_id == "0":
                    continue

                # Parse minutes from "MM:SS" format
                min_str = p.get("Minutes", "0:00")
                try:
                    parts = str(min_str).split(":")
                    minutes = int(parts[0]) + int(parts[1]) / 60 if len(parts) == 2 else float(min_str)
                except (ValueError, IndexError):
                    minutes = 0

                off_poss = p.get("OffPoss", 0) or 0
                def_poss = p.get("DefPoss", 0) or 0
                total_poss = off_poss + def_poss

                records.append({
                    "PLAYER_ID": int(entity_id),
                    "PBP_MINUTES": minutes,
                    "PBP_POSS": total_poss,
                    "PBP_OFF_POSS": off_poss,
                    "PBP_REB_CHANCES": (p.get("Rebounds", 0) or 0) + (p.get("DefRebounds", 0) or 0) * 0.3,
                    "PBP_POT_AST": (p.get("Assists", 0) or 0) * 1.7,
                    "PBP_USAGE": p.get("Usage", 0) or 0,
                    "PBP_SHOT_QUALITY": p.get("ShotQualityAvg", 0) or 0,
                })
        import time
        time.sleep(0.2)  # rate limit between games

    if not records:
        logger.warning("PBP Stats returned 0 player records across %d games", len(games))
        return pd.DataFrame(columns=["PLAYER_ID", "PBP_MINUTES", "POSS_PER_GAME_EST", "PBP_REB_CHANCES", "PBP_POT_AST"])

    df = pd.DataFrame(records)
    logger.info("PBP Stats: %d player-game records from %d games", len(df), len(games))
    return (
        df.groupby("PLAYER_ID", as_index=False)
        .mean(numeric_only=True)
        .rename(columns={"PBP_POSS": "POSS_PER_GAME_EST"})
    )

def get_player_game_logs_pbpstats(config: PipelineConfig, last_n_games: int = 5) -> pd.DataFrame:
    """
    Pull per-player game logs from PBP Stats as a fallback when nba_api times out.
    Uses the get-game-stats endpoint we already know works.

    Returns per-player rolling aggregates from the last N games:
    - recent_min_avg, recent_min_std
    - recent_pts_avg, recent_ast_avg, recent_reb_avg, recent_fg3m_avg
    - recent_pts_median, recent_ast_median, recent_reb_median, recent_fg3m_median
    - Plus per-game raw logs for streak detection
    """
    import requests as req
    import time as _time

    GAMES_URL = "https://api.pbpstats.com/get-games/nba"
    GAME_STATS_URL = "https://api.pbpstats.com/get-game-stats"

    headers = {
        "User-Agent": config.user_agent,
        "Accept": "application/json",
    }

    try:
        games = []
        for season_type in ["Playoffs", "Regular Season"]:
            resp = req.get(
                GAMES_URL,
                params={"Season": config.season, "SeasonType": season_type},
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            found = data.get("results") or data.get("games") or []
            if found:
                games = found[-last_n_games:]
                logger.info("PBP Stats game logs using %s (%d games)", season_type, len(games))
                break
    except Exception as exc:
        logger.warning("PBP Stats game logs - games list failed: %s", exc)
        return pd.DataFrame()

    player_games = []

    for game in games:
        game_id = game.get("GameId") or game.get("game_id")
        game_date = game.get("Date", "")
        if not game_id:
            continue
        try:
            detail_resp = req.get(
                GAME_STATS_URL,
                params={"Type": "Player", "GameId": game_id},
                headers=headers,
                timeout=30,
            )
            detail_resp.raise_for_status()
            details = detail_resp.json()
        except Exception as exc:
            logger.warning("PBP Stats game log %s failed: %s", game_id, exc)
            continue

        for side in ["Home", "Away"]:
            side_data = details.get("stats", {}).get(side, {})
            players = side_data.get("FullGame") or side_data.get("1") or []
            team_abbrev = details.get(f"{side.lower()}_team_abbreviation", "")

            for p in players:
                if p.get("Name") == "Team":
                    continue
                entity_id = p.get("EntityId")
                if not entity_id or entity_id == "0":
                    continue

                # Parse minutes
                min_str = p.get("Minutes", "0:00")
                try:
                    parts = str(min_str).split(":")
                    minutes = int(parts[0]) + int(parts[1]) / 60 if len(parts) == 2 else float(min_str)
                except (ValueError, IndexError):
                    minutes = 0

                # Derive points from available fields
                # PBP Stats has FG2M, FG3M (or Arc3FGM), FTM
                fg2m = p.get("FG2M", 0) or 0
                fg3m = p.get("FG3M", 0) or p.get("Arc3FGM", 0) or 0
                ftm = p.get("FTM", 0) or p.get("FreeThrowsMade", 0) or 0
                pts = (fg2m * 2) + (fg3m * 3) + ftm

                ast = p.get("Assists", 0) or 0
                reb = p.get("Rebounds", 0) or 0
                off_reb = p.get("OffRebounds", 0) or 0
                def_reb = p.get("DefRebounds", 0) or 0
                if reb == 0 and (off_reb or def_reb):
                    reb = off_reb + def_reb

                fga = (p.get("FG2A", 0) or 0) + (p.get("FG3A", 0) or p.get("Arc3FGA", 0) or 0)
                fg3a = p.get("FG3A", 0) or p.get("Arc3FGA", 0) or 0

                player_games.append({
                    "PLAYER_ID": int(entity_id),
                    "PLAYER_NAME": p.get("Name", ""),
                    "GAME_DATE": game_date,
                    "GAME_ID": game_id,
                    "TEAM": team_abbrev,
                    "MIN": minutes,
                    "PTS": pts,
                    "AST": ast,
                    "REB": reb,
                    "FGA": fga,
                    "FG3A": fg3a,
                    "FG3M": fg3m,
                    "FTM": ftm,
                })

        _time.sleep(0.2)

    if not player_games:
        logger.warning("PBP Stats game logs: 0 records")
        return pd.DataFrame()

    raw = pd.DataFrame(player_games)
    logger.info("PBP Stats game logs: %d player-game records from %d games", len(raw), len(games))

    # Compute per-player aggregates
    agg = raw.groupby("PLAYER_ID").agg(
        recent_games=("PLAYER_ID", "count"),
        recent_min_avg=("MIN", "mean"),
        recent_min_std=("MIN", "std"),
        recent_pts_avg=("PTS", "mean"),
        recent_pts_median=("PTS", "median"),
        recent_ast_avg=("AST", "mean"),
        recent_ast_median=("AST", "median"),
        recent_reb_avg=("REB", "mean"),
        recent_reb_median=("REB", "median"),
        recent_fg3m_avg=("FG3M", "mean"),
        recent_fg3m_median=("FG3M", "median"),
        recent_pts_std=("PTS", "std"),
        recent_ast_std=("AST", "std"),
        recent_reb_std=("REB", "std"),
        recent_fg3m_std=("FG3M", "std"),
        recent_fga_avg=("FGA", "mean"),
        recent_fg3a_avg=("FG3A", "mean"),
        recent_fta_avg=("FTM", "mean"),
    ).reset_index()

    for col in agg.columns:
        if col.endswith("_std"):
            agg[col] = agg[col].fillna(0)

    return agg

def get_injury_report(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    """Pull NBA injury report from RotoWire's internal JSON endpoint."""
    import requests as req

    try:
        resp = req.get(
            "https://www.rotowire.com/basketball/tables/injury-report.php",
            params={"team": "ALL", "pos": "ALL"},
            headers={
                "User-Agent": config.user_agent,
                "Referer": "https://www.rotowire.com/basketball/injury-report.php",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        rows = []
        for entry in data:
            player_name = entry.get("player", "").strip()
            status_raw = entry.get("status", "").strip().upper()
            team = entry.get("team", "").strip()
            injury = entry.get("injury", "").strip()

            status = "UNKNOWN"
            if "OUT FOR SEASON" in status_raw:
                status = "OUT"
            elif "OUT" in status_raw:
                status = "OUT"
            elif "DOUBTFUL" in status_raw or "QUESTIONABLE" in status_raw:
                status = "DOUBTFUL"
            elif "GTD" in status_raw or "GAME TIME" in status_raw:
                status = "QUESTIONABLE"
            elif "PROBABLE" in status_raw:
                status = "PROBABLE"
            elif "DAY-TO-DAY" in status_raw or "DTD" in status_raw:
                status = "QUESTIONABLE"

            if player_name and status in ("OUT", "DOUBTFUL", "QUESTIONABLE"):
                rows.append({
                    "PLAYER_NAME": player_name,
                    "TEAM_ABBREVIATION": team,
                    "STATUS": status,
                    "INJURY": injury,
                })

        if rows:
            logger.info("RotoWire injury report: %d entries (%d OUT, %d DOUBTFUL, %d QUESTIONABLE)",
                len(rows),
                sum(1 for r in rows if r["STATUS"] == "OUT"),
                sum(1 for r in rows if r["STATUS"] == "DOUBTFUL"),
                sum(1 for r in rows if r["STATUS"] == "QUESTIONABLE"),
            )
            return pd.DataFrame(rows)
    except Exception as exc:
        logger.warning("RotoWire injury report failed: %s", exc)

    return pd.DataFrame(columns=["PLAYER_NAME", "TEAM_ABBREVIATION", "STATUS", "INJURY"])
# These two don't go through stats.nba.com so they're imported from the original ingestion module
from .ingestion import (  # noqa: E402
    get_today_matchups,
    get_starting_lineups_scrape,
)
