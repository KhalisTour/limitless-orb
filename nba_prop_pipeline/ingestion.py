from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .clients import CachedHTTPClient
from .config import PipelineConfig

NBA_BASE = "https://stats.nba.com/stats"
PBPSTATS_GAMES_URL = "https://api.pbpstats.com/get-games/nba"
PBPSTATS_GAME_URL = "https://api.pbpstats.com/get-game-details/nba"


def _to_dataframe(result: Dict[str, Any]) -> pd.DataFrame:
    result_sets = result.get("resultSets") or result.get("resultSet")
    if isinstance(result_sets, dict):
        headers = result_sets["headers"]
        rows = result_sets["rowSet"]
        return pd.DataFrame(rows, columns=headers)

    if isinstance(result_sets, list) and result_sets:
        headers = result_sets[0]["headers"]
        rows = result_sets[0]["rowSet"]
        return pd.DataFrame(rows, columns=headers)

    return pd.DataFrame()


def get_player_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    payload = client.get_json(
        f"{NBA_BASE}/leaguedashplayerstats",
        params={
            "Season": config.season,
            "SeasonType": config.season_type,
            "PerMode": "PerGame",
            "MeasureType": "Base",
            "LeagueID": "00",
        },
        namespace="nba_player_stats",
    )
    return _to_dataframe(payload)


def get_tracking_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    payload = client.get_json(
        f"{NBA_BASE}/leaguedashptstats",
        params={
            "Season": config.season,
            "SeasonType": config.season_type,
            "PerMode": "PerGame",
            "PtMeasureType": "SpeedDistance",
            "LeagueID": "00",
        },
        namespace="nba_tracking_speed",
    )
    speed = _to_dataframe(payload)

    payload_touches = client.get_json(
        f"{NBA_BASE}/leaguedashptstats",
        params={
            "Season": config.season,
            "SeasonType": config.season_type,
            "PerMode": "PerGame",
            "PtMeasureType": "Possessions",
            "LeagueID": "00",
        },
        namespace="nba_tracking_possessions",
    )
    touches = _to_dataframe(payload_touches)

    if not speed.empty and not touches.empty:
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
        available = [col for col in keep if col in touches.columns]
        return speed.merge(touches[available], on=[c for c in ["PLAYER_ID", "TEAM_ID"] if c in touches.columns], how="left")

    return touches if not touches.empty else speed


def get_rebounding_tracking_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    payload = client.get_json(
        f"{NBA_BASE}/leaguedashptstats",
        params={
            "Season": config.season,
            "SeasonType": config.season_type,
            "PerMode": "PerGame",
            "PtMeasureType": "Rebounding",
            "LeagueID": "00",
        },
        namespace="nba_tracking_rebounding",
    )
    return _to_dataframe(payload)


def get_team_defense_stats(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    payload = client.get_json(
        f"{NBA_BASE}/leaguedashteamstats",
        params={
            "Season": config.season,
            "SeasonType": config.season_type,
            "PerMode": "PerGame",
            "MeasureType": "Defense",
            "LeagueID": "00",
        },
        namespace="nba_team_defense",
    )
    defense = _to_dataframe(payload)

    payload_opp = client.get_json(
        f"{NBA_BASE}/leaguedashteamstats",
        params={
            "Season": config.season,
            "SeasonType": config.season_type,
            "PerMode": "PerGame",
            "MeasureType": "Opponent",
            "LeagueID": "00",
        },
        namespace="nba_team_opponent",
    )
    opp = _to_dataframe(payload_opp)

    if defense.empty:
        return opp
    if opp.empty:
        return defense

    common = [c for c in ["TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION"] if c in defense.columns and c in opp.columns]
    keep_opp = [c for c in ["TEAM_ID", "OPP_AST", "OPP_FGA", "OPP_FG3A", "OPP_PTS", "DEF_RATING", "PACE"] if c in opp.columns]
    return defense.merge(opp[keep_opp], on=[c for c in common if c in keep_opp or c in ["TEAM_ID"]], how="left")


def get_today_matchups(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    payload = client.get_json(config.today_scoreboard_url, namespace="nba_scoreboard")
    games = payload.get("scoreboard", {}).get("games", [])
    rows: List[Dict[str, Any]] = []
    for game in games:
        game_id = game.get("gameId")
        home = game.get("homeTeam", {}).get("teamTricode")
        away = game.get("awayTeam", {}).get("teamTricode")
        game_date = game.get("gameEt") or game.get("gameTimeUTC")
        if home and away:
            rows.append({"GAME_ID": game_id, "TEAM_ABBREVIATION": home, "OPPONENT_ABBREVIATION": away, "GAME_DATETIME": game_date})
            rows.append({"GAME_ID": game_id, "TEAM_ABBREVIATION": away, "OPPONENT_ABBREVIATION": home, "GAME_DATETIME": game_date})
    return pd.DataFrame(rows)


def get_starting_lineups_scrape(config: PipelineConfig) -> pd.DataFrame:
    """Fallback lineup scraper from ESPN's daily NBA lineups page."""
    date_string = dt.datetime.utcnow().strftime("%Y%m%d")
    url = f"https://www.espn.com/nba/lineups/_/date/{date_string}"
    try:
        response = requests.get(url, timeout=config.timeout_seconds, headers={"User-Agent": config.user_agent})
        response.raise_for_status()
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=["TEAM_ABBREVIATION", "PLAYER_NAME", "IS_CONFIRMED_STARTER"])

    soup = BeautifulSoup(response.text, "html.parser")
    lineup_cards = soup.select("section.Card")
    rows: List[Dict[str, Any]] = []
    for card in lineup_cards:
        team_nodes = card.select("div.TeamLineup__TeamName")
        player_nodes = card.select("li.TeamLineup__Player")
        if len(team_nodes) != 2 or len(player_nodes) < 10:
            continue
        teams = [node.get_text(strip=True).split()[-1].upper()[:3] for node in team_nodes]
        for idx, team in enumerate(teams):
            segment = player_nodes[idx * 5 : (idx + 1) * 5]
            for player in segment:
                rows.append(
                    {
                        "TEAM_ABBREVIATION": team,
                        "PLAYER_NAME": player.get_text(strip=True),
                        "IS_CONFIRMED_STARTER": True,
                    }
                )

    return pd.DataFrame(rows)


def get_pbpstats_possessions(client: CachedHTTPClient, config: PipelineConfig) -> pd.DataFrame:
    payload = client.get_json(
        PBPSTATS_GAMES_URL,
        params={"Season": config.season, "SeasonType": "Regular Season"},
        namespace="pbpstats_games",
    )
    games = payload.get("games", [])[-15:]
    records: List[Dict[str, Any]] = []

    for game in games:
        game_id = game.get("game_id") or game.get("GameId")
        if not game_id:
            continue
        details = client.get_json(
            PBPSTATS_GAME_URL,
            params={"GameId": game_id},
            namespace="pbpstats_game_details",
        )

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
        return pd.DataFrame(columns=["PLAYER_ID", "PBP_MINUTES", "PBP_POSS", "PBP_REB_CHANCES", "PBP_POT_AST"])

    df = pd.DataFrame(records)
    return (
        df.groupby("PLAYER_ID", as_index=False)
        .mean(numeric_only=True)
        .rename(columns={"PBP_POSS": "POSS_PER_GAME_EST"})
    )
