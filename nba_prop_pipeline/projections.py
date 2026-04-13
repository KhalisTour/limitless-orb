from __future__ import annotations

import numpy as np
import pandas as pd


def add_projections(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    possessions_scale = out["projected_minutes"] / out["projected_minutes"].replace(0, np.nan).median()
    possessions_scale = possessions_scale.fillna(1.0).clip(lower=0.7, upper=1.35)

    out["shot_volume_proj"] = out["fga"] * possessions_scale * (0.8 + out["usage_rate"])
    out["three_attempts_proj"] = out["fg3a"] * possessions_scale * out["opp_3pa_factor"]

    two_pa = (out["shot_volume_proj"] - out["three_attempts_proj"]).clip(lower=0)
    two_pt_pct = (out["fg_pct"] - (out["fg3_pct"] * 0.38)).clip(lower=0.38, upper=0.62)

    points_from_twos = two_pa * two_pt_pct * 2
    points_from_threes = out["three_attempts_proj"] * out["fg3_pct"] * 3

    out["points_proj"] = (points_from_twos + points_from_threes) * out["opp_points_factor"]

    assist_conversion = np.where(out["potential_assists"] > 0, (out.get("AST", 0) / out["potential_assists"]).clip(0.35, 0.78), 0.58)
    out["assists_proj"] = out["potential_assists"] * assist_conversion * out["opp_assist_factor"]

    rebound_conversion = np.where(out["rebound_chances"] > 0, (out.get("REB", 0) / out["rebound_chances"]).clip(0.30, 0.72), 0.48)
    out["rebounds_proj"] = out["rebound_chances"] * rebound_conversion * out["opp_reb_factor"]

    out["threes_proj"] = out["three_attempts_proj"] * out["fg3_pct"]

    return out
