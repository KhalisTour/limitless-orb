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
import re
import sys
import traceback
import unicodedata
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


def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[.\-']", " ", name)
    parts = name.lower().split()
    if len(parts) >= 2 and "," in name:
        comma_parts = name.split(",", 1)
        parts = comma_parts[1].strip().lower().split() + comma_parts[0].strip().lower().split()
    return " ".join(parts)


def _load_batter_id_map(date: str) -> dict:
    """Build batter MLBAM ID -> normalized name from the snapshot CSV."""
    snap_dir = REPO / "data" / "snapshots" / date
    bat_path = snap_dir / f"batters_{date}.csv"
    if not bat_path.exists():
        return {}
    df = pd.read_csv(bat_path)
    id_col = None
    for c in ["player_id", "batter_id", "mlbam_id", "id"]:
        if c in df.columns:
            id_col = c
            break
        for col in df.columns:
            if col.lower() == c.lower():
                id_col = col
                break
        if id_col:
            break
    name_col = None
    for c in ["player_name", "name", "full_name", "last_name, first_name"]:
        if c in df.columns:
            name_col = c
            break
    if not id_col or not name_col:
        return {}
    result = {}
    for _, row in df.iterrows():
        pid = row[id_col]
        if pd.notna(pid):
            raw = str(row[name_col])
            if "," in raw:
                parts = raw.split(",", 1)
                flipped = f"{parts[1].strip()} {parts[0].strip()}"
            else:
                flipped = raw
            result[int(pid)] = _normalize_name(flipped)
    return result


def pull_actuals(date: str) -> pd.DataFrame:
    """Per-(game, batter_id) HR counts from Statcast for `date`.
    NOTE: Statcast player_name is the PITCHER, not the batter.
    We use the numeric `batter` column (MLBAM ID) instead.
    """
    from pybaseball import statcast
    from pybaseball import cache
    cache.enable()
    df = statcast(start_dt=date, end_dt=date)
    if df.empty:
        return pd.DataFrame(columns=["game_pk", "batter_id", "hr_count"])
    df = df[df["events"] == "home_run"]
    if df.empty:
        return pd.DataFrame(columns=["game_pk", "batter_id", "hr_count"])
    agg = (df.groupby(["game_pk", "batter"])
             .size().reset_index(name="hr_count")
             .rename(columns={"batter": "batter_id"}))
    return agg


def score_for_date(date: str):
    preds_path = DATA_DIR / f"predictions_{date}.json"
    if not preds_path.exists():
        raise RuntimeError(f"No predictions file at {preds_path}")
    preds = json.loads(preds_path.read_text())

    actuals = pull_actuals(date)
    id_map = _load_batter_id_map(date)

    # Resolve batter IDs to names and build lookups
    if actuals.empty:
        print(f"[score] WARNING: Statcast returned NO home runs for {date}")
    else:
        print(f"[score] Statcast HRs: {len(actuals)} batter-game rows")
        for _, r in actuals.iterrows():
            bid = int(r["batter_id"])
            name = id_map.get(bid, f"?id={bid}")
            print(f"  game_pk={r['game_pk']}  batter_id={bid}  -> {name}  (count={r['hr_count']})")

    actual_by_game = {}
    actual_by_name = {}
    for r in actuals.itertuples():
        bid = int(r.batter_id)
        name = id_map.get(bid)
        if name is None:
            continue
        actual_by_game[(int(r.game_pk), name)] = int(r.hr_count)
        actual_by_name[name] = actual_by_name.get(name, 0) + int(r.hr_count)

    pred_game_ids = set()
    rows = []
    for g in preds.get("games", []):
        gid = g["game_id"]
        pred_game_ids.add(int(gid))
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
                hn = _normalize_name(hitter)
                actual = actual_by_game.get((int(gid), hn))
                if actual is None:
                    actual = actual_by_name.get(hn, 0)
                rows.append(dict(
                    game_date=date, game_id=gid, side=side,
                    hitter=hitter, opp_pitcher=sb.get("pitcher"),
                    p_hr=p_hr, p_per_pa=entry.get("p_per_pa"),
                    exp_pa=entry.get("exp_pa"),
                    actual_hr=int(actual >= 1), actual_hr_count=actual,
                    residual=int(actual >= 1) - p_hr,
                ))

    # Debug: check for game_id overlap
    actual_game_pks = set(int(r.game_pk) for r in actuals.itertuples()) if not actuals.empty else set()
    overlap = pred_game_ids & actual_game_pks
    print(f"[score] pred game_ids: {sorted(pred_game_ids)}")
    print(f"[score] actual game_pks: {sorted(actual_game_pks)}")
    print(f"[score] overlap: {len(overlap)} games  matched_hrs: {sum(1 for r in rows if r['actual_hr'] > 0)}")

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
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
    }
    if len(set(y)) >= 2:
        metrics["auc"] = float(roc_auc_score(y, p))
    else:
        metrics["auc"] = None
        metrics["auc_note"] = "need both classes in y_true"
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
