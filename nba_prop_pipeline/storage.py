from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

DB_PATH = Path("data/nba_props.db")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projections (
            run_date TEXT,
            player_id INTEGER,
            player_name TEXT,
            team TEXT,
            opponent TEXT,
            game_id TEXT,
            points_proj REAL,
            assists_proj REAL,
            rebounds_proj REAL,
            threes_proj REAL,
            P_points_ge_20 REAL,
            P_assists_ge_6 REAL,
            P_rebounds_ge_8 REAL,
            P_3pm_ge_3 REAL,
            MC_P_points_ge_20 REAL,
            MC_P_assists_ge_6 REAL,
            MC_P_rebounds_ge_8 REAL,
            MC_P_3pm_ge_3 REAL,
            projected_minutes REAL,
            usage_rate REAL,
            fga REAL,
            fg3a REAL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_projections_run_player_game
        ON projections(run_date, player_id, game_id)
        """
    )
    return conn


def insert_projections(df: pd.DataFrame, run_date: str) -> None:
    if df.empty:
        return

    records = pd.DataFrame(
        {
            "run_date": run_date,
            "player_id": pd.to_numeric(df.get("PLAYER_ID"), errors="coerce"),
            "player_name": df.get("PLAYER_NAME"),
            "team": df.get("TEAM_ABBREVIATION"),
            "opponent": df.get("OPPONENT_ABBREVIATION"),
            "game_id": df.get("GAME_ID").astype(str) if "GAME_ID" in df.columns else None,
            "points_proj": df.get("points_proj"),
            "assists_proj": df.get("assists_proj"),
            "rebounds_proj": df.get("rebounds_proj"),
            "threes_proj": df.get("threes_proj"),
            "P_points_ge_20": df.get("P_points_ge_20"),
            "P_assists_ge_6": df.get("P_assists_ge_6"),
            "P_rebounds_ge_8": df.get("P_rebounds_ge_8"),
            "P_3pm_ge_3": df.get("P_3pm_ge_3"),
            "MC_P_points_ge_20": df.get("MC_P_points_ge_20"),
            "MC_P_assists_ge_6": df.get("MC_P_assists_ge_6"),
            "MC_P_rebounds_ge_8": df.get("MC_P_rebounds_ge_8"),
            "MC_P_3pm_ge_3": df.get("MC_P_3pm_ge_3"),
            "projected_minutes": df.get("projected_minutes"),
            "usage_rate": df.get("usage_rate"),
            "fga": df.get("fga"),
            "fg3a": df.get("fg3a"),
        }
    )
    records = records.dropna(subset=["player_id", "game_id"]).copy()
    records["player_id"] = records["player_id"].astype(int)

    conn = _connect()
    with conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO projections (
                run_date, player_id, player_name, team, opponent, game_id,
                points_proj, assists_proj, rebounds_proj, threes_proj,
                P_points_ge_20, P_assists_ge_6, P_rebounds_ge_8, P_3pm_ge_3,
                MC_P_points_ge_20, MC_P_assists_ge_6, MC_P_rebounds_ge_8, MC_P_3pm_ge_3,
                projected_minutes, usage_rate, fga, fg3a
            ) VALUES (
                :run_date, :player_id, :player_name, :team, :opponent, :game_id,
                :points_proj, :assists_proj, :rebounds_proj, :threes_proj,
                :P_points_ge_20, :P_assists_ge_6, :P_rebounds_ge_8, :P_3pm_ge_3,
                :MC_P_points_ge_20, :MC_P_assists_ge_6, :MC_P_rebounds_ge_8, :MC_P_3pm_ge_3,
                :projected_minutes, :usage_rate, :fga, :fg3a
            )
            """,
            records.to_dict(orient="records"),
        )
    conn.close()


def get_projections(run_date: Optional[str] = None, player_name: Optional[str] = None) -> pd.DataFrame:
    conn = _connect()
    query = "SELECT * FROM projections WHERE 1=1"
    params = []
    if run_date:
        query += " AND run_date = ?"
        params.append(run_date)
    if player_name:
        query += " AND player_name = ?"
        params.append(player_name)
    query += " ORDER BY run_date DESC, player_name ASC"
    out = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return out
