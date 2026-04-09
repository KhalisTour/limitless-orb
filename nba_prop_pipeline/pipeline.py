from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from .clients import CachedHTTPClient
from .config import PipelineConfig
from .exporter import export_json
from .features import build_feature_table
from .ingestion import (
    get_pbpstats_possessions,
    get_player_stats,
    get_rebounding_tracking_stats,
    get_starting_lineups_scrape,
    get_team_defensive_scheme_stats,
    get_team_defense_stats,
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
) -> Path:
    config = config or PipelineConfig()
    client = CachedHTTPClient(config, force_refresh=force_refresh)

    player_stats = get_player_stats(client, config)
    tracking = get_tracking_stats(client, config)
    reb_tracking = get_rebounding_tracking_stats(client, config)
    team_defense = get_team_defense_stats(client, config)
    team_scheme = get_team_defensive_scheme_stats(client, config)
    matchups = get_today_matchups(client, config)
    pbp_possessions = get_pbpstats_possessions(client, config)

    feature_df = build_feature_table(
        player_stats=player_stats,
        tracking_stats=tracking,
        reb_tracking=reb_tracking,
        team_defense=team_defense,
        team_scheme_stats=team_scheme,
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

    projected = add_projections(feature_df, config=config)
    probabilistic = add_probabilities(projected)

    if include_monte_carlo:
        probabilistic = add_monte_carlo_probs(probabilistic)

    if odds_map:
        probabilistic = attach_ev(probabilistic, odds_map)

    if probabilistic.empty:
        logger.warning("No rows generated for today's slate. Output will be an empty file.")

    return export_json(probabilistic, config.output_json)
