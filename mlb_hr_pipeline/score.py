"""
Phase 4 — Score predictions against actual game outcomes.

For a given date (default: yesterday), load predictions_<date>.json, pull that
day's Statcast PAs, count HRs per batter per game, and emit per-prediction
calibration rows appended to data/calibration_log.csv. Also reports running
Brier, log-loss, AUC across the full log.

A "prediction" here is a per-game probability that a hitter goes deep at least
once. We use sim's p_at_least_one_hr (the natural unit for "did Hitter X homer
in Game Y?"). If sim wasn't run, fall back to 1 - (1 - p_per_pa) ** exp_pa.
"""

import json
import math
import sys
import traceback
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score


REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
LOG_PATH = DATA_DIR / "calibration_log.csv"


def _pred_p_hr_in_game(per_hitter_entry, sim_block, hitter):
    if sim_block and isinstance(sim_block, dict) and "p_at_least_one_hr" in sim_block:
        v = sim_block["p_at_least_one_hr"].get(hitter)
        if v is not None:
            return float(v)
    ppa = per_hitter_entry.get("p_per_pa")
    exp_pa = per_hitter_entry.get("exp_pa", 4.2)
    if ppa is None:
        return None
    return 1.0 - (1.0 - float(ppa)) ** float(exp_pa)


def pull_actuals(date: str) -> pd.DataFrame:
    """Per-(game, batter_name) HR counts from Statcast for `date`."""
    from pybaseball import statcast
    from pybaseball import cache
    cache.enable()
    df = statcast(start_dt=date, end_dt=date)
    if df.empty:
        return pd.DataFrame(columns=["game_pk", "batter_name", "hr_count"])
    df = df[df["events"] == "home_run"]
    name_col = "player_name" if "player_name" in df.columns else "batter"
    agg = (df.groupby(["game_pk", name_col])
             .size().reset_index(name="hr_count")
             .rename(columns={name_col: "batter_name"}))
    return agg


def score_for_date(date: str):
    preds_path = DATA_DIR / f"predictions_{date}.json"
    if not preds_path.exists():
        raise RuntimeError(f"No predictions file at {preds_path}")
    preds = json.loads(preds_path.read_text())

    actuals = pull_actuals(date)
    actuals["batter_name_lc"] = actuals["batter_name"].astype(str).str.lower()
    actual_lookup = {(int(r.game_pk), r.batter_name_lc): int(r.hr_count)
                     for r in actuals.itertuples()}

    rows = []
    for g in preds.get("games", []):
        gid = g["game_id"]
        for side in ("away", "home"):
            sb = g["sides"].get(side, {})
            if not isinstance(sb, dict) or sb.get("error"):
                continue
            sim_block = sb.get("sim") or {}
            for hitter, entry in sb.get("per_hitter", {}).items():
                if "error" in entry:
                    continue
                p_hr = _pred_p_hr_in_game(entry, sim_block, hitter)
                if p_hr is None:
                    continue
                actual = actual_lookup.get((int(gid), hitter.lower()), 0)
                rows.append(dict(
                    game_date=date, game_id=gid, side=side,
                    hitter=hitter, opp_pitcher=sb.get("pitcher"),
                    p_hr=p_hr, p_per_pa=entry.get("p_per_pa"),
                    exp_pa=entry.get("exp_pa"),
                    actual_hr=int(actual >= 1), actual_hr_count=actual,
                    residual=int(actual >= 1) - p_hr,
                ))

    if not rows:
        print(f"[score] no scoreable predictions for {date}")
        return None

    new_df = pd.DataFrame(rows)
    if LOG_PATH.exists():
        old = pd.read_csv(LOG_PATH)
        # avoid duplicates on rerun
        key_cols = ["game_date", "game_id", "side", "hitter"]
        old_keys = set(map(tuple, old[key_cols].astype(str).values.tolist()))
        new_df_filt = new_df[~new_df[key_cols].astype(str).apply(tuple, axis=1).isin(old_keys)]
        full = pd.concat([old, new_df_filt], ignore_index=True)
    else:
        full = new_df
    full.to_csv(LOG_PATH, index=False)
    print(f"[score] appended {len(new_df)} rows to {LOG_PATH}  (total={len(full)})")

    # Running metrics
    y = full["actual_hr"].values.astype(int)
    p = full["p_hr"].clip(1e-6, 1 - 1e-6).values.astype(float)
    metrics = {
        "n": int(len(y)),
        "rate_actual": float(y.mean()),
        "rate_pred": float(p.mean()),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p)),
    }
    try:
        metrics["auc"] = float(roc_auc_score(y, p))
    except Exception as e:
        metrics["auc"] = None
        metrics["auc_err"] = str(e)
    print(f"[score] running metrics: {json.dumps(metrics, indent=2)}")
    (DATA_DIR / "calibration_metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            d = sys.argv[1]
        else:
            d = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        score_for_date(d)
    except Exception:
        traceback.print_exc()
        print("\nRerun: python score.py [YYYY-MM-DD]", file=sys.stderr)
        sys.exit(1)
