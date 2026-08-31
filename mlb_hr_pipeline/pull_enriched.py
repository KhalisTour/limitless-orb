"""
Enriched Statcast pull — adds spin, movement, release point, spray,
and environmental columns needed for the spherical spin experiment
and distance residual model.

Requires network access (pybaseball → Baseball Savant).
Run locally: python pull_enriched.py [start_date] [end_date]

Outputs:
  data/pitches_enriched_<start>_<end>.csv  — full enriched pitch-level data
  data/pitches_enriched.csv                — stable symlink for downstream scripts
"""

import sys
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
DATA_DIR.mkdir(exist_ok=True)

# Original columns from pull_pitch_level.py
BASE_COLS = [
    "game_pk", "game_date", "batter", "pitcher", "player_name",
    "stand", "p_throws", "inning", "events", "description",
    "pitch_type", "pitch_name", "zone", "plate_x", "plate_z",
    "balls", "strikes",
    "launch_angle", "launch_speed", "hit_distance_sc", "bb_type",
    "home_team", "away_team", "release_speed", "at_bat_number",
]

# New columns for spin/movement/release/spray/environment
ENRICHMENT_COLS = [
    # Pitch spin & movement (spherical spin experiment)
    "release_spin_rate",       # RPM
    "spin_axis",               # degrees (0-360, gyro orientation)
    "pfx_x",                   # horizontal movement (inches, pitcher POV)
    "pfx_z",                   # vertical movement (inches, induced)

    # Release point (arm slot characterization)
    "release_pos_x",           # horizontal release (feet)
    "release_pos_z",           # vertical release (feet)
    "release_pos_y",           # extension toward plate (feet)
    "release_extension",       # extension (feet, if available)

    # Batted ball spray (distance residual model)
    "hc_x",                    # hit coordinate X (pixels, 0-250 scale)
    "hc_y",                    # hit coordinate Y (pixels, 0-250 scale)

    # Swing metrics (Statcast 2024+)
    "bat_speed",               # bat speed at contact (mph)
    "swing_length",            # swing length (feet)
]

ALL_COLS = BASE_COLS + ENRICHMENT_COLS


def pull_statcast_enriched(start_dt: str, end_dt: str) -> pd.DataFrame:
    """Pull pitch-by-pitch Statcast data with enriched columns.

    pybaseball returns all ~90+ Savant columns; we keep only what we need.
    The pull is chunked by week to avoid timeouts on long date ranges.
    """
    from pybaseball import statcast, cache
    cache.enable()

    start = pd.Timestamp(start_dt)
    end = pd.Timestamp(end_dt)

    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + pd.Timedelta(days=6), end)
        s_str = cursor.strftime("%Y-%m-%d")
        e_str = chunk_end.strftime("%Y-%m-%d")
        print(f"[enriched] pulling {s_str} → {e_str} ...")

        try:
            df = statcast(start_dt=s_str, end_dt=e_str)
            if df is not None and len(df) > 0:
                chunks.append(df)
                print(f"           {len(df):,} pitches, {len(df.columns)} cols")
            else:
                print(f"           empty")
        except Exception as e:
            print(f"           ERROR: {e}")

        cursor = chunk_end + pd.Timedelta(days=1)

    if not chunks:
        raise RuntimeError(f"No data returned for {start_dt} → {end_dt}")

    raw = pd.concat(chunks, ignore_index=True)
    print(f"[enriched] total raw pitches: {len(raw):,}")

    # Report which enrichment columns are present
    present = [c for c in ENRICHMENT_COLS if c in raw.columns]
    missing = [c for c in ENRICHMENT_COLS if c not in raw.columns]
    print(f"[enriched] enrichment columns present: {len(present)}/{len(ENRICHMENT_COLS)}")
    for c in present:
        n_valid = raw[c].notna().sum()
        pct = n_valid / len(raw) * 100
        print(f"           {c:<25s} {n_valid:>10,} non-null ({pct:.1f}%)")
    if missing:
        print(f"[enriched] missing from Savant: {missing}")

    keep = [c for c in ALL_COLS if c in raw.columns]
    df = raw[keep].copy()

    # Compute spray_angle from hc_x, hc_y if both present
    if "hc_x" in df.columns and "hc_y" in df.columns:
        # Savant coordinates: home plate ≈ (125, 200), CF ≈ (125, 50)
        # spray_angle: 0° = dead center, negative = pull (LHH→right, RHH→left)
        dx = df["hc_x"] - 125.42
        dy = 198.27 - df["hc_y"]
        df["spray_angle"] = np.degrees(np.arctan2(dx, dy))
        n_spray = df["spray_angle"].notna().sum()
        print(f"[enriched] computed spray_angle: {n_spray:,} values")

    # Compute movement_angle (theta) and movement_magnitude for S² mapping
    if "pfx_x" in df.columns and "pfx_z" in df.columns:
        # Convert movement to polar: (pfx_x, pfx_z) → (r, theta)
        # theta ∈ [0, 2π), phi from magnitude mapped to [0, π/2]
        pfx_r = np.sqrt(df["pfx_x"]**2 + df["pfx_z"]**2)
        pfx_theta = np.arctan2(df["pfx_x"], df["pfx_z"])  # angle from vertical
        df["movement_r"] = pfx_r
        df["movement_theta"] = pfx_theta
        n_move = df["movement_r"].notna().sum()
        print(f"[enriched] computed movement polar: {n_move:,} values")

    return df


def report_enrichment(df: pd.DataFrame):
    """Print summary statistics for the enriched columns."""
    print(f"\n{'='*60}")
    print("ENRICHMENT SUMMARY")
    print(f"{'='*60}")
    print(f"Total pitches: {len(df):,}")

    sections = {
        "Spin & Movement": ["release_spin_rate", "spin_axis", "pfx_x", "pfx_z",
                            "movement_r", "movement_theta"],
        "Release Point": ["release_pos_x", "release_pos_z", "release_pos_y",
                          "release_extension"],
        "Batted Ball Spray": ["hc_x", "hc_y", "spray_angle", "hit_distance_sc"],
        "Swing Metrics": ["bat_speed", "swing_length"],
    }

    for section, cols in sections.items():
        print(f"\n  {section}:")
        for c in cols:
            if c not in df.columns:
                print(f"    {c:<25s}  NOT IN DATA")
                continue
            valid = df[c].dropna()
            if len(valid) == 0:
                print(f"    {c:<25s}  all null")
            else:
                print(f"    {c:<25s}  n={len(valid):>10,}  "
                      f"mean={valid.mean():>8.2f}  "
                      f"std={valid.std():>7.2f}  "
                      f"[{valid.min():>8.2f}, {valid.max():>8.2f}]")

    # HR-specific: show enrichment coverage on batted balls and home runs
    batted = df[df["bb_type"].notna()]
    hrs = df[df["events"] == "home_run"]
    print(f"\n  Batted balls: {len(batted):,}")
    print(f"  Home runs: {len(hrs):,}")

    for c in ["hc_x", "hc_y", "spray_angle", "bat_speed", "swing_length"]:
        if c in df.columns:
            bb_pct = batted[c].notna().mean() * 100 if len(batted) > 0 else 0
            hr_pct = hrs[c].notna().mean() * 100 if len(hrs) > 0 else 0
            print(f"    {c:<25s}  batted={bb_pct:.1f}%  HR={hr_pct:.1f}%")


def main():
    today = dt.date.today()
    if len(sys.argv) >= 3:
        start_dt = sys.argv[1]
        end_dt = sys.argv[2]
    else:
        season = today.year if today.month >= 4 else today.year - 1
        start_dt = f"{season}-04-01"
        end_dt = f"{season}-09-30" if today.month >= 10 else today.isoformat()
        print(f"[enriched] defaulting to {start_dt} → {end_dt}")

    print(f"[enriched] pulling enriched Statcast: {start_dt} → {end_dt}")
    print(f"[enriched] this may take 30-60 min for a full season\n")

    df = pull_statcast_enriched(start_dt, end_dt)
    report_enrichment(df)

    # Save
    out_dated = DATA_DIR / f"pitches_enriched_{start_dt}_{end_dt}.csv"
    df.to_csv(out_dated, index=False)
    print(f"\n[enriched] wrote {out_dated} ({len(df):,} rows, {len(df.columns)} cols)")

    out_stable = DATA_DIR / "pitches_enriched.csv"
    df.to_csv(out_stable, index=False)
    print(f"[enriched] wrote {out_stable}")

    # Also list which downstream experiments are now unblocked
    print(f"\n{'='*60}")
    print("UNBLOCKED EXPERIMENTS")
    print(f"{'='*60}")
    has_pfx = "pfx_x" in df.columns and df["pfx_x"].notna().any()
    has_spray = "spray_angle" in df.columns and df["spray_angle"].notna().any()
    has_swing = "bat_speed" in df.columns and df["bat_speed"].notna().any()

    if has_pfx:
        print("  ✓ Spherical spin experiment (pfx_x, pfx_z → S² harmonics)")
        print("    Run: python spherical_spin_experiment.py")
    else:
        print("  ✗ Spherical spin experiment — missing pfx_x/pfx_z")

    if has_spray:
        print("  ✓ Distance residual model (spray_angle + EV + LA → distance residual)")
        print("    Run: python distance_residual_experiment.py")
    else:
        print("  ✗ Distance residual model — missing hc_x/hc_y")

    if has_swing:
        print("  ✓ Swing mechanics factor (bat_speed, swing_length as tensor factors)")
    else:
        print("  ✗ Swing mechanics — bat_speed/swing_length not available (needs 2024+ data)")

    if "release_spin_rate" in df.columns and df["release_spin_rate"].notna().any():
        print("  ✓ Spin rate as continuous pitcher factor in CP model")


if __name__ == "__main__":
    main()
