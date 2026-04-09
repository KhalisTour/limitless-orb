from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd
from scipy.stats import poisson


@dataclass
class PropLineSet:
    points: float = 20
    assists: float = 6
    rebounds: float = 8
    threes: float = 3


def prob_over(line: float, mean: float) -> float:
    mean = max(float(mean), 0.01)
    return float(1 - poisson.cdf(line - 1, mean))


def add_probabilities(df: pd.DataFrame, lines: Optional[PropLineSet] = None) -> pd.DataFrame:
    lines = lines or PropLineSet()
    out = df.copy()

    out["P_points_ge_20"] = out["points_proj"].apply(lambda x: prob_over(lines.points, x))
    out["P_assists_ge_6"] = out["assists_proj"].apply(lambda x: prob_over(lines.assists, x))
    out["P_rebounds_ge_8"] = out["rebounds_proj"].apply(lambda x: prob_over(lines.rebounds, x))
    out["P_3pm_ge_3"] = out["threes_proj"].apply(lambda x: prob_over(lines.threes, x))

    return out


def add_monte_carlo_probs(df: pd.DataFrame, n_sims: int = 3000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    out = df.copy()

    mc_cols = {
        "MC_P_points_ge_20": ("points_proj", 20),
        "MC_P_assists_ge_6": ("assists_proj", 6),
        "MC_P_rebounds_ge_8": ("rebounds_proj", 8),
        "MC_P_3pm_ge_3": ("threes_proj", 3),
    }

    for new_col, (mean_col, line) in mc_cols.items():
        probs = []
        for mean in out[mean_col].fillna(0.01):
            draws = rng.poisson(lam=max(mean, 0.01), size=n_sims)
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
        "assists_ge_6": "P_assists_ge_6",
        "rebounds_ge_8": "P_rebounds_ge_8",
        "threes_ge_3": "P_3pm_ge_3",
    }

    for market, prob_col in markets.items():
        ev_col = f"EV_{market}"
        values = []
        for _, row in out.iterrows():
            odds = odds_map.get(row.get("PLAYER_NAME", ""), {}).get(market)
            values.append(_ev(row[prob_col], odds))
        out[ev_col] = values

    return out
