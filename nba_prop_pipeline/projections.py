from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PipelineConfig


def compute_zone_based_points_projection(df: pd.DataFrame, config: PipelineConfig) -> pd.Series:
    """
    Final points projection with three additive layers:
      1. Zone-based field goal points (zone_FGA × zone_FG% × point_value, summed across zones)
      2. Free throw points (FTA × FT% scaled to projected minutes)
      3. Scoring engine multiplier (usage × TS interaction, captures star scorers)

    Formula:
        fg_projection = zone_points × minutes_scale × dampened_strategy_factor × engine_factor
        final = (fg_projection + ft_points) × opp_points_factor

    The engine_factor uses a usage × TS interaction: high-usage players only get
    the full boost if they maintain TS% above league average. This prevents
    volume gunners from being projected like real stars.
    """
    def _safe(col: str, default: float = 0.0) -> pd.Series:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").fillna(default)
        return pd.Series(default, index=df.index)

    # Layer 1: Zone-based field goal points (already computed upstream as zone_points_raw)
    zone_points = _safe("zone_points_raw")

    # Minutes scaling: scale season-pace zone points to tonight's projected minutes
    proj_min = _safe("projected_minutes", 30)
    season_min = _safe("MIN", 30)
    season_min = season_min.where(season_min > 0, 30)
    minutes_scale = (proj_min / season_min).clip(lower=0.5, upper=1.5)

    # Layer 2: Free throw points (currently missing from the projection — adds 5-8 pts/game for stars)
    fta = _safe("FTA", 2.0)
    ft_pct = _safe("FT_PCT", 0.78)
    ft_points = fta * ft_pct * minutes_scale

    # Layer 3: Scoring engine multiplier
    # Uses usage × TS interaction. High-usage players only get full boost if TS is elite.
    # Volume gunners with low TS get a floored, partial boost.
    usage = _safe("usage_rate", 0.22)
    ts = _safe("TS_PCT", config.league_avg_ts)
    usage_above_starter = (usage - 0.25).clip(lower=0)
    ts_modulation = (1 + (ts - config.league_avg_ts) * config.ts_modulation_strength).clip(lower=0.5)
    engine_factor = 1 + (usage_above_starter * config.usage_boost_strength * ts_modulation)

    # Existing factors
    strategy = _safe("dampened_strategy_factor", 1.0)
    opp_factor = _safe("opp_points_factor", 1.0)

    # Combine: FG side gets engine boost; FT side does not (free throws are already efficiency-priced)
    fg_projection = zone_points * minutes_scale * strategy * engine_factor
    final = (fg_projection + ft_points) * opp_factor

    return final


def add_projections(df: pd.DataFrame, *, config: PipelineConfig) -> pd.DataFrame:
    out = df.copy()

    # Scheme-aware minutes risk adjustment (additive on top of baseline minute projection).
    out["projected_minutes"] = out["projected_minutes"] * (
        1
        - (out.get("blowout_risk_penalty", 0) * config.blowout_minutes_penalty_weight)
        - (out.get("foul_risk_penalty", 0) * config.foul_minutes_penalty_weight)
    )
    out["projected_minutes"] = out["projected_minutes"].clip(lower=10)

    possessions_scale = out["projected_minutes"] / out["projected_minutes"].replace(0, np.nan).median()
    possessions_scale = possessions_scale.fillna(1.0).clip(lower=0.7, upper=1.35)

    out["shot_volume_proj"] = out["fga"] * possessions_scale * (0.8 + out["usage_rate"])
    out["three_attempts_proj"] = out["fg3a"] * possessions_scale * out["opp_3pa_factor"]

    two_pa = (out["shot_volume_proj"] - out["three_attempts_proj"]).clip(lower=0)
    two_pt_pct = (out["fg_pct"] - (out["fg3_pct"] * 0.38)).clip(lower=0.38, upper=0.62)

    points_from_twos = two_pa * two_pt_pct * 2
    points_from_threes = out["three_attempts_proj"] * out["fg3_pct"] * 3

    trap_delta = (out.get("opp_trap_blitz_rate", config.league_avg_trap_blitz_rate) - config.league_avg_trap_blitz_rate) / 100
    hedge_delta = (out.get("opp_hedge_rate", config.league_avg_hedge_rate) - config.league_avg_hedge_rate) / 100
    scoring_scheme_adj = 1 + (config.scoring_trap_blitz_weight * trap_delta)
    legacy_points_proj = (points_from_twos + points_from_threes) * out["opp_points_factor"] * scoring_scheme_adj

    # New zone + play type based projection (overrides the legacy formula)
    zone_proj = compute_zone_based_points_projection(out, config)
    # Keep the legacy projection for backtest comparison
    out["points_proj_legacy"] = legacy_points_proj
    out["points_proj"] = np.where(zone_proj > 0, zone_proj, legacy_points_proj)

    assist_conversion = np.where(out["potential_assists"] > 0, (out.get("AST", 0) / out["potential_assists"]).clip(0.35, 0.78), 0.58)
    assist_scheme_adj = 1 + (config.assist_trap_blitz_weight * trap_delta) + (config.assist_hedge_weight * hedge_delta)
    out["assists_proj"] = out["potential_assists"] * assist_conversion * out["opp_assist_factor"] * assist_scheme_adj

    rebound_conversion = np.where(out["rebound_chances"] > 0, (out.get("REB", 0) / out["rebound_chances"]).clip(0.30, 0.72), 0.48)
    stretch_boost = 1 + (config.rebound_stretch_big_weight * out.get("high5_stretch_indicator", 0))
    out["rebounds_proj"] = out["rebound_chances"] * rebound_conversion * out["opp_reb_factor"] * stretch_boost

    out["threes_proj"] = out["three_attempts_proj"] * out["fg3_pct"]

    return out
