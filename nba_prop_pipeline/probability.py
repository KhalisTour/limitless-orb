from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson


# ---------------------------------------------------------------------------
# Dispersion constants — control tail thickness per stat category.
#
# Lower dispersion = more overdispersion = fatter tails.
# Poisson is the limit as dispersion -> infinity.
#
# Empirical NBA variance/mean ratios by stat type:
#   Points:   ~2.5–3.5  (hot/cold shooting quarters, foul trouble, blowouts)
#   Assists:  ~2.0–3.0  (rhythm-dependent, game script drives pass volume)
#   Rebounds: ~1.5–2.0  (least streaky — mostly positional, pace-driven)
#   3PM:      ~2.5–4.0  (most streaky — hot hand is strongest for 3s)
#
# Dispersion = mean / (variance/mean - 1), so:
#   variance/mean = 2.5  =>  dispersion = mean / 1.5  (for mean=25 pts: d≈16.7)
#   variance/mean = 3.0  =>  dispersion = mean / 2.0  (for mean=25 pts: d≈12.5)
#
# We use FIXED dispersion values rather than mean-dependent ones for simplicity.
# These are calibrated against empirical NBA game-to-game variance.
# ---------------------------------------------------------------------------
DISPERSION_POINTS = 12.0     # moderate overdispersion
DISPERSION_ASSISTS = 5.0     # assists are quite streaky
DISPERSION_REBOUNDS = 8.0    # rebounds are the least streaky counting stat
DISPERSION_THREES = 3.5      # 3PM is the most streaky (hot hand effect)


@dataclass
class PropLineSet:
    points: float = 20
    assists: float = 6
    rebounds: float = 8
    threes: float = 3


def prob_over_negbin(line: float, mean: float, dispersion: float) -> float:
    """
    Probability of a counting stat >= line using negative binomial distribution.

    The negative binomial captures overdispersion (hot/cold streakiness) that
    Poisson misses. Variance = mean + mean^2/dispersion, which is always >= mean.

    Parameters
    ----------
    line : threshold to clear (e.g. 20 for points o20)
    mean : projected stat value (e.g. 26.5 projected points)
    dispersion : controls tail thickness. Lower = fatter tails.
                 As dispersion -> infinity, converges to Poisson.
    """
    mean = max(float(mean), 0.01)
    dispersion = max(float(dispersion), 0.5)
    # scipy parameterization: n = dispersion, p = n / (n + mean)
    n = dispersion
    p = n / (n + mean)
    return float(1 - nbinom.cdf(line - 1, n, p))


def prob_over_poisson(line: float, mean: float) -> float:
    """Legacy Poisson probability — kept for comparison / fallback."""
    mean = max(float(mean), 0.01)
    return float(1 - poisson.cdf(line - 1, mean))


def add_probabilities(df: pd.DataFrame, lines: Optional[PropLineSet] = None) -> pd.DataFrame:
    """
    Compute analytical over-line probabilities using negative binomial.

    Produces both the primary NB probabilities and Poisson for comparison.
    Includes ladder thresholds for 3PM (3+, 4+, 5+, 6+, 7+) and
    additional points ladders (15+, 20+, 25+, 30+).
    """
    lines = lines or PropLineSet()
    out = df.copy()

    # --- Points ---
    out["P_points_ge_15"] = out["points_proj"].apply(lambda x: prob_over_negbin(15, x, DISPERSION_POINTS))
    out["P_points_ge_20"] = out["points_proj"].apply(lambda x: prob_over_negbin(20, x, DISPERSION_POINTS))
    out["P_points_ge_25"] = out["points_proj"].apply(lambda x: prob_over_negbin(25, x, DISPERSION_POINTS))
    out["P_points_ge_30"] = out["points_proj"].apply(lambda x: prob_over_negbin(30, x, DISPERSION_POINTS))

    # --- Assists ---
    out["P_assists_ge_4"] = out["assists_proj"].apply(lambda x: prob_over_negbin(4, x, DISPERSION_ASSISTS))
    out["P_assists_ge_6"] = out["assists_proj"].apply(lambda x: prob_over_negbin(6, x, DISPERSION_ASSISTS))
    out["P_assists_ge_8"] = out["assists_proj"].apply(lambda x: prob_over_negbin(8, x, DISPERSION_ASSISTS))
    out["P_assists_ge_10"] = out["assists_proj"].apply(lambda x: prob_over_negbin(10, x, DISPERSION_ASSISTS))

    # --- Rebounds ---
    out["P_rebounds_ge_6"] = out["rebounds_proj"].apply(lambda x: prob_over_negbin(6, x, DISPERSION_REBOUNDS))
    out["P_rebounds_ge_8"] = out["rebounds_proj"].apply(lambda x: prob_over_negbin(8, x, DISPERSION_REBOUNDS))
    out["P_rebounds_ge_10"] = out["rebounds_proj"].apply(lambda x: prob_over_negbin(10, x, DISPERSION_REBOUNDS))
    out["P_rebounds_ge_12"] = out["rebounds_proj"].apply(lambda x: prob_over_negbin(12, x, DISPERSION_REBOUNDS))

    # --- 3PM ---
    out["P_3pm_ge_2"] = out["threes_proj"].apply(lambda x: prob_over_negbin(2, x, DISPERSION_THREES))
    out["P_3pm_ge_3"] = out["threes_proj"].apply(lambda x: prob_over_negbin(3, x, DISPERSION_THREES))
    out["P_3pm_ge_4"] = out["threes_proj"].apply(lambda x: prob_over_negbin(4, x, DISPERSION_THREES))
    out["P_3pm_ge_5"] = out["threes_proj"].apply(lambda x: prob_over_negbin(5, x, DISPERSION_THREES))
    out["P_3pm_ge_6"] = out["threes_proj"].apply(lambda x: prob_over_negbin(6, x, DISPERSION_THREES))
    out["P_3pm_ge_7"] = out["threes_proj"].apply(lambda x: prob_over_negbin(7, x, DISPERSION_THREES))

    # --- Poisson reference columns (for comparison / UI toggle) ---
    out["P_poisson_points_ge_20"] = out["points_proj"].apply(lambda x: prob_over_poisson(20, x))
    out["P_poisson_assists_ge_6"] = out["assists_proj"].apply(lambda x: prob_over_poisson(6, x))
    out["P_poisson_rebounds_ge_8"] = out["rebounds_proj"].apply(lambda x: prob_over_poisson(8, x))
    out["P_poisson_3pm_ge_3"] = out["threes_proj"].apply(lambda x: prob_over_poisson(3, x))

    return out


def add_monte_carlo_probs(df: pd.DataFrame, n_sims: int = 5000, seed: int = 7) -> pd.DataFrame:
    """
    Monte Carlo simulation using negative binomial draws for all stat types.

    Increased from 3,000 to 5,000 sims for more stable tail estimates.
    Each stat category uses its own dispersion parameter.
    """
    rng = np.random.default_rng(seed)
    out = df.copy()

    mc_cols = {
        # (projection_column, line, dispersion)
        "MC_P_points_ge_15": ("points_proj", 15, DISPERSION_POINTS),
        "MC_P_points_ge_20": ("points_proj", 20, DISPERSION_POINTS),
        "MC_P_points_ge_25": ("points_proj", 25, DISPERSION_POINTS),
        "MC_P_points_ge_30": ("points_proj", 30, DISPERSION_POINTS),
        "MC_P_assists_ge_4": ("assists_proj", 4, DISPERSION_ASSISTS),
        "MC_P_assists_ge_6": ("assists_proj", 6, DISPERSION_ASSISTS),
        "MC_P_assists_ge_8": ("assists_proj", 8, DISPERSION_ASSISTS),
        "MC_P_assists_ge_10": ("assists_proj", 10, DISPERSION_ASSISTS),
        "MC_P_rebounds_ge_6": ("rebounds_proj", 6, DISPERSION_REBOUNDS),
        "MC_P_rebounds_ge_8": ("rebounds_proj", 8, DISPERSION_REBOUNDS),
        "MC_P_rebounds_ge_10": ("rebounds_proj", 10, DISPERSION_REBOUNDS),
        "MC_P_rebounds_ge_12": ("rebounds_proj", 12, DISPERSION_REBOUNDS),
        "MC_P_3pm_ge_2": ("threes_proj", 2, DISPERSION_THREES),
        "MC_P_3pm_ge_3": ("threes_proj", 3, DISPERSION_THREES),
        "MC_P_3pm_ge_4": ("threes_proj", 4, DISPERSION_THREES),
        "MC_P_3pm_ge_5": ("threes_proj", 5, DISPERSION_THREES),
        "MC_P_3pm_ge_6": ("threes_proj", 6, DISPERSION_THREES),
        "MC_P_3pm_ge_7": ("threes_proj", 7, DISPERSION_THREES),
    }

    for new_col, (mean_col, line, dispersion) in mc_cols.items():
        probs = []
        for mean in out[mean_col].fillna(0.01):
            mean = max(float(mean), 0.01)
            n_param = max(dispersion, 0.5)
            p_param = n_param / (n_param + mean)
            draws = rng.negative_binomial(n_param, p_param, size=n_sims)
            probs.append(float((draws >= line).mean()))
        out[new_col] = probs

    return out


def attach_ev(
    df: pd.DataFrame,
    odds_map: Dict[str, Dict[str, float]],
) -> pd.DataFrame:
    """Attach expected value for decimal odds keyed by player and market.

    odds_map format:
    {
      "Nikola Jokic": {"points_ge_20": 1.83, "assists_ge_6": 1.95}
    }
    """
    out = df.copy()

    def _ev(prob: float, odds: Optional[float]) -> Optional[float]:
        if odds is None:
            return None
        return float((prob * (odds - 1)) - (1 - prob))

    markets = {
        "points_ge_20": "P_points_ge_20",
        "points_ge_25": "P_points_ge_25",
        "points_ge_30": "P_points_ge_30",
        "assists_ge_6": "P_assists_ge_6",
        "assists_ge_8": "P_assists_ge_8",
        "assists_ge_10": "P_assists_ge_10",
        "rebounds_ge_8": "P_rebounds_ge_8",
        "rebounds_ge_10": "P_rebounds_ge_10",
        "rebounds_ge_12": "P_rebounds_ge_12",
        "threes_ge_3": "P_3pm_ge_3",
        "threes_ge_4": "P_3pm_ge_4",
        "threes_ge_5": "P_3pm_ge_5",
        "threes_ge_6": "P_3pm_ge_6",
        "threes_ge_7": "P_3pm_ge_7",
    }

    for market, prob_col in markets.items():
        if prob_col not in out.columns:
            continue
        ev_col = f"EV_{market}"
        values = []
        for _, row in out.iterrows():
            odds = odds_map.get(row.get("PLAYER_NAME", ""), {}).get(market)
            values.append(_ev(row[prob_col], odds))
        out[ev_col] = values

    return out