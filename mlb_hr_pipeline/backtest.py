"""
Phase 0 — Backtest harness.

Pull a Statcast date range, collapse pitch rows to per-PA rows with a HR (0/1)
label, write the table + base-rate summary.

Outputs (in ./data/):
  - backtest_<start>_<end>.csv   one row per PA, including label `hr`
  - base_rates.json              HR/XBH/hit/TB per PA, plus HR by LA bin, pitch_type, count

The pull is the expensive step (~30-40 min for a full season). pybaseball's
on-disk cache is enabled so re-runs are cheap.
"""

import os
import sys
import json
import traceback
import datetime as dt
from pathlib import Path

import pandas as pd
import numpy as np


REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
LOG_DIR = REPO / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)


def pull_statcast(start_dt: str, end_dt: str) -> pd.DataFrame:
    """Pull pitch-by-pitch data and trim to the columns we need."""
    from pybaseball import statcast
    from pybaseball import cache
    cache.enable()  # ~/.pybaseball_cache by default
    print(f"[backtest] pulling Statcast {start_dt} -> {end_dt} (cached)")
    df = statcast(start_dt=start_dt, end_dt=end_dt)
    print(f"[backtest] pitches: {len(df):,}  cols: {len(df.columns)}")
    keep = [c for c in [
        "game_pk", "game_date", "batter", "pitcher", "events", "description",
        "launch_angle", "launch_speed", "hit_distance_sc",
        "pitch_type", "pitch_name", "release_speed",
        "balls", "strikes", "inning", "at_bat_number",
        "stand", "p_throws", "bb_type",
        "player_name",  # batter name on most builds
    ] if c in df.columns]
    return df[keep]


def collapse_to_pa(pitches: pd.DataFrame) -> pd.DataFrame:
    """Reduce pitch rows to one row per plate appearance.

    Statcast's `at_bat_number` uniquely identifies a PA within a game, so the
    correct grouping is (game_pk, at_bat_number). The last pitch carries the
    terminal `events` field.
    """
    if "at_bat_number" not in pitches.columns:
        raise RuntimeError("Statcast frame missing at_bat_number; cannot group PAs")

    # Sort so .last() picks the truly-last pitch
    pitches = pitches.sort_values(["game_pk", "at_bat_number", "inning", "balls", "strikes"])
    grouped = pitches.groupby(["game_pk", "at_bat_number"], as_index=False)
    pa = grouped.agg(
        game_date=("game_date", "first"),
        batter=("batter", "first"),
        pitcher=("pitcher", "first"),
        batter_name=("player_name", "first") if "player_name" in pitches.columns else ("batter", "first"),
        stand=("stand", "first"),
        p_throws=("p_throws", "first"),
        inning=("inning", "first"),
        events=("events", "last"),
        last_pitch_type=("pitch_type", "last"),
        last_balls=("balls", "last"),
        last_strikes=("strikes", "last"),
        launch_angle=("launch_angle", "last"),
        launch_speed=("launch_speed", "last"),
        hit_distance=("hit_distance_sc", "last"),
        bb_type=("bb_type", "last"),
    )
    # Drop rows where the PA didn't end with a terminal event (rare, partial games).
    pa = pa[pa["events"].notna()].copy()
    pa["hr"] = (pa["events"] == "home_run").astype(int)
    return pa


# Outcome vocabulary shared with fit_model_xbh.py / fit_model_hit.py so the
# three fits and the published baselines can never disagree about what an XBH is.
XBH_EVENTS = ("double", "triple", "home_run")
HIT_EVENTS = ("single", "double", "triple", "home_run")
TOTAL_BASES = {"single": 1, "double": 2, "triple": 3, "home_run": 4}


def base_rates(pa: pd.DataFrame) -> dict:
    """Compute HR / XBH / hit / TB per PA overall, plus HR by LA bin, pitch type, count."""
    out = {"n_pa": int(len(pa)), "n_hr": int(pa["hr"].sum())}
    out["hr_per_pa"] = float(pa["hr"].mean())

    # The XBH / hit / total-base baselines the other three models anchor on.
    # models_xbh.py and models_hit.py used to carry guessed constants (0.09 and
    # 0.245 against measured 0.076 and 0.218), which put every hitter on the
    # slate well above the real league rate before a single feature was read.
    # They are derived from the same `events` column as `hr`, so there is no
    # reason for them not to be measured here alongside it.
    ev = pa["events"]
    out["n_xbh"] = int(ev.isin(XBH_EVENTS).sum())
    out["n_hit"] = int(ev.isin(HIT_EVENTS).sum())
    out["xbh_per_pa"] = float(ev.isin(XBH_EVENTS).mean())
    out["hit_per_pa"] = float(ev.isin(HIT_EVENTS).mean())
    out["tb_per_pa"] = float(ev.map(TOTAL_BASES).fillna(0).mean())

    # LA bins (degrees). Only meaningful when the ball is in play.
    la_bins = [-90, 0, 10, 20, 30, 40, 90]
    pa = pa.copy()
    pa["la_bin"] = pd.cut(pa["launch_angle"], la_bins)
    la_summary = pa.groupby("la_bin", observed=True)["hr"].agg(["count", "mean"]).reset_index()
    out["hr_by_la_bin"] = [
        {"la_bin": str(r["la_bin"]), "n": int(r["count"]), "hr_rate": float(r["mean"])}
        for _, r in la_summary.iterrows()
    ]

    # Pitch type (only rows where pa-ending pitch type is known)
    pt = pa.dropna(subset=["last_pitch_type"])
    pt_summary = (pt.groupby("last_pitch_type")["hr"]
                    .agg(["count", "mean"]).reset_index()
                    .sort_values("count", ascending=False))
    out["hr_by_pitch_type"] = [
        {"pitch_type": r["last_pitch_type"], "n": int(r["count"]), "hr_rate": float(r["mean"])}
        for _, r in pt_summary.iterrows()
    ]

    # Count at end-of-PA (these are the "decisive" counts)
    pa["count_str"] = pa["last_balls"].astype("Int64").astype(str) + "-" + pa["last_strikes"].astype("Int64").astype(str)
    cnt = (pa.groupby("count_str")["hr"]
             .agg(["count", "mean"]).reset_index()
             .sort_values("count", ascending=False))
    out["hr_by_count"] = [
        {"count": r["count_str"], "n": int(r["count"]), "hr_rate": float(r["mean"])}
        for _, r in cnt.iterrows()
    ]
    return out


def main(start_dt: str = None, end_dt: str = None):
    today = dt.date.today()
    # Season: May 1 of the most recent season with completed games through "today".
    # If we're before April, fall back to last season.
    if start_dt is None:
        season = today.year if today.month >= 4 else today.year - 1
        start_dt = f"{season}-05-01"
    if end_dt is None:
        end_dt = today.isoformat()

    pitches = pull_statcast(start_dt, end_dt)
    if pitches.empty:
        raise RuntimeError(f"No Statcast rows returned for {start_dt} -> {end_dt}")

    pa = collapse_to_pa(pitches)
    out_csv = DATA_DIR / f"backtest_{start_dt}_{end_dt}.csv"
    pa.to_csv(out_csv, index=False)
    print(f"[backtest] wrote {out_csv}  PAs={len(pa):,}  HRs={int(pa['hr'].sum()):,}")

    # Also write a stable filename for downstream phases
    stable = DATA_DIR / "backtest.csv"
    pa.to_csv(stable, index=False)
    print(f"[backtest] wrote {stable}")

    summary = base_rates(pa)
    summary["start_dt"] = start_dt
    summary["end_dt"] = end_dt
    summary["generated_at"] = dt.datetime.utcnow().isoformat() + "Z"
    rates_path = DATA_DIR / "base_rates.json"
    rates_path.write_text(json.dumps(summary, indent=2))
    print(f"[backtest] wrote {rates_path}")
    print(json.dumps({k: summary[k] for k in
                      ["n_pa", "n_hr", "hr_per_pa", "xbh_per_pa",
                       "hit_per_pa", "tb_per_pa"]}, indent=2))
    return summary


if __name__ == "__main__":
    try:
        s = sys.argv[1] if len(sys.argv) > 1 else None
        e = sys.argv[2] if len(sys.argv) > 2 else None
        main(s, e)
    except Exception:
        traceback.print_exc()
        print("\nRerun: python backtest.py [YYYY-MM-DD start] [YYYY-MM-DD end]", file=sys.stderr)
        sys.exit(1)
