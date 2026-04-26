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


def get_player_game_logs(client: CachedHTTPClient, config: PipelineConfig, last_n_games: int = 15) -> pd.DataFrame:
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
    Bypasses CachedHTTPClient to avoid header contamination.
    Uses plain requests with minimal headers.
    """
    import requests as req

    games_url = "https://api.pbpstats.com/get-games/nba"
    details_url = "https://api.pbpstats.com/get-game-details/nba"

    headers = {
        "User-Agent": config.user_agent,
        "Accept": "application/json",
    }

    empty_cols = ["PLAYER_ID", "PBP_MINUTES", "POSS_PER_GAME_EST", "PBP_REB_CHANCES", "PBP_POT_AST"]

    try:
        resp = req.get(
            games_url,
            params={"Season": config.season, "SeasonType": "Regular Season"},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        games = resp.json().get("games", [])[-15:]
    except Exception as exc:
        logger.warning("PBP Stats games list failed: %s", exc)
        return pd.DataFrame(columns=empty_cols)

    records = []
    for game in games:
        game_id = game.get("game_id") or game.get("GameId")
        if not game_id:
            continue
        try:
            detail_resp = req.get(
                details_url,
                params={"GameId": game_id},
                headers=headers,
                timeout=30,
            )
            detail_resp.raise_for_status()
            details = detail_resp.json()
        except Exception as exc:
            logger.warning("PBP Stats game %s details failed: %s", game_id, exc)
            continue

        boxscore = details.get("boxscore", {})
        players = boxscore.get("players", {}) if isinstance(boxscore, dict) else {}
        for player_id, statline in players.items():
            records.append(
                {
                    "PLAYER_ID": int(player_id),
                    "PBP_MINUTES": statline.get("minutes", 0),
                    "PBP_POSS": statline.get("possessions", 0),
                    "PBP_REB_CHANCES": statline.get("rebound_chances", 0),
                    "PBP_POT_AST": statline.get("potential_assists", 0),
                }
            )

    if not records:
        logger.warning("PBP Stats returned 0 player records across %d games", len(games))
        return pd.DataFrame(columns=empty_cols)

    df = pd.DataFrame(records)
    logger.info("PBP Stats: %d player-game records from %d games", len(df), len(games))
    return (
        df.groupby("PLAYER_ID", as_index=False)
        .mean(numeric_only=True)
        .rename(columns={"PBP_POSS": "POSS_PER_GAME_EST"})
    )


def get_injury_report(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    """Scrape ESPN injury page. Returns DataFrame with PLAYER_NAME and STATUS columns."""
    import requests as req
    from bs4 import BeautifulSoup

    try:
        url = "https://www.espn.com/nba/injuries"
        response = req.get(url, timeout=config.timeout_seconds, headers={"User-Agent": config.user_agent})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        rows = []
        team_sections = soup.select("div.ResponsiveTable")
        for section in team_sections:
            team_header = section.find_previous("h3")
            if not team_header:
                team_header = section.find_previous("div", class_="injuries__teamName")
            team_name = team_header.get_text(strip=True) if team_header else ""

            table_rows = section.select("tbody tr")
            for tr in table_rows:
                cells = tr.select("td")
                if len(cells) >= 2:
                    player_name = cells[0].get_text(strip=True)
                    status_cell = cells[1].get_text(strip=True).upper()

                    status = "UNKNOWN"
                    if "OUT" in status_cell or status_cell == "O":
                        status = "OUT"
                    elif "DOUBTFUL" in status_cell or status_cell == "D":
                        status = "DOUBTFUL"
                    elif "QUESTIONABLE" in status_cell or status_cell == "Q":
                        status = "QUESTIONABLE"
                    elif "DAY-TO-DAY" in status_cell or "DTD" in status_cell:
                        status = "QUESTIONABLE"

                    if player_name and status in ("OUT", "DOUBTFUL", "QUESTIONABLE"):
                        rows.append({
                            "PLAYER_NAME": player_name,
                            "TEAM_NAME": team_name,
                            "STATUS": status,
                        })

        if rows:
            logger.info("Pulled %d injury entries from ESPN", len(rows))
            return pd.DataFrame(rows)
    except Exception as exc:
        logger.warning("ESPN injury scrape failed: %s", exc)

    return pd.DataFrame(columns=["PLAYER_NAME", "TEAM_NAME", "STATUS"])
# These two don't go through stats.nba.com so they're imported from the original ingestion module
from .ingestion import (  # noqa: E402
    get_today_matchups,
    get_starting_lineups_scrape,
)
