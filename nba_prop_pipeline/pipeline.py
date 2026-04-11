from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .clients import CachedHTTPClient
from .config import PipelineConfig
from .exporter import export_json
from .features import build_feature_table
from .ingestion_nba_api import (
    get_pbpstats_possessions,
    get_positional_defense_stats,
    get_opponent_stats_for_slate,
    get_player_stats,
    get_rebounding_tracking_stats,
    get_starting_lineups_scrape,
    get_team_defensive_scheme_stats,
    get_today_matchups,
    get_tracking_stats,
)
from .probability import add_monte_carlo_probs, add_probabilities, attach_ev
from .projections import add_projections

logger = logging.getLogger(__name__)


def run_daily_pipeline(
    *,
    config: Optional[PipelineConfig] = None,
    odds_map: Optional[Dict[str, Dict[str, float]]] = None,
    include_monte_carlo: bool = True,
    force_refresh: bool = False,
    require_confirmed_starters: bool = False,
    write_to_db: bool = True,
) -> Path:
    config = config or PipelineConfig()
    client = CachedHTTPClient(config, force_refresh=force_refresh)

    matchups = get_today_matchups(client, config)
    player_stats = get_player_stats(client, config)
    tracking = get_tracking_stats(client, config)
    reb_tracking = get_rebounding_tracking_stats(client, config)
    team_defense = get_opponent_stats_for_slate(client, config, matchups)
    positional_defense = pd.DataFrame()
    try:
        positional_defense = get_positional_defense_stats(client, config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Positional defense pull failed, using team-wide defense only: %s", exc)
    team_scheme = get_team_defensive_scheme_stats(client, config)
    pbp_possessions = get_pbpstats_possessions(client, config)

    feature_df = build_feature_table(
        player_stats=player_stats,
        tracking_stats=tracking,
        reb_tracking=reb_tracking,
        team_defense=team_defense,
        team_scheme_stats=team_scheme,
        positional_defense_stats=positional_defense,
        matchups=matchups,
        pbp_possessions=pbp_possessions,
        config=config,
    )

    # Optional starting lineup filter enhancement.
    starters = get_starting_lineups_scrape(config)
    if not starters.empty and {"PLAYER_NAME", "TEAM_ABBREVIATION"}.issubset(feature_df.columns):
        feature_df = feature_df.merge(
            starters[["TEAM_ABBREVIATION", "PLAYER_NAME", "IS_CONFIRMED_STARTER"]],
            on=["TEAM_ABBREVIATION", "PLAYER_NAME"],
            how="left",
        )
        if require_confirmed_starters:
            teams_with_confirmed = set(starters["TEAM_ABBREVIATION"].dropna().unique().tolist())
            keep_mask = (
                ~feature_df["TEAM_ABBREVIATION"].isin(teams_with_confirmed)
                | feature_df["IS_CONFIRMED_STARTER"].fillna(False)
            )
            feature_df = feature_df[keep_mask].copy()

    projected = add_projections(feature_df, config=config)
    projected = projected[projected["projected_minutes"] >= 23]
    probabilistic = add_probabilities(projected)

    if include_monte_carlo:
        probabilistic = add_monte_carlo_probs(probabilistic)

    if odds_map:
        probabilistic = attach_ev(probabilistic, odds_map)

    if probabilistic.empty:
        logger.warning("No starter-level rows generated for today's slate. Output will be an empty file.")

    output_path = export_json(probabilistic, config.output_json)
    if write_to_db:
        try:
            from .storage import insert_projections

            insert_projections(probabilistic, run_date=dt.date.today().isoformat())
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB persistence failed; continuing without crash: %s", exc)

    return output_path
