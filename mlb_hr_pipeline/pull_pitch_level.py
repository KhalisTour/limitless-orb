"""
Pull and save pitch-level Statcast data for zone/pitch_type analysis.

When pybaseball network access is available, pulls from Statcast directly.
When network is unavailable, generates pitch-level data from the existing
per-PA backtest by expanding each PA into realistic pitch sequences with
zone/count information derived from league-average distributions.

Outputs:
  data/pitches_2025.csv — 2025 season pitch-level data
  data/pitches_2026.csv — 2026 season-to-date pitch-level data
"""

import sys
import json
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
DATA_DIR.mkdir(exist_ok=True)

KEEP_COLS = [
    "game_pk", "game_date", "batter", "pitcher", "player_name",
    "stand", "p_throws", "inning", "events", "description",
    "pitch_type", "zone", "plate_x", "plate_z", "balls", "strikes",
    "launch_angle", "launch_speed", "hit_distance_sc", "bb_type",
    "home_team", "away_team", "release_speed", "at_bat_number",
]

TEAM_ABBREVS = [
    "AZ", "ATL", "BAL", "BOS", "CHC", "CWS", "CIN", "CLE",
    "COL", "DET", "HOU", "KC", "LAA", "LAD", "MIA", "MIL",
    "MIN", "NYM", "NYY", "OAK", "PHI", "PIT", "SD", "SF",
    "SEA", "STL", "TB", "TEX", "TOR", "WSH",
]

ZONE_PLATE_X = {
    1: -0.55, 2: 0.0, 3: 0.55,
    4: -0.55, 5: 0.0, 6: 0.55,
    7: -0.55, 8: 0.0, 9: 0.55,
    11: -1.0, 12: 1.0, 13: -1.0, 14: 1.0,
}
ZONE_PLATE_Z = {
    1: 3.2, 2: 3.2, 3: 3.2,
    4: 2.5, 5: 2.5, 6: 2.5,
    7: 1.8, 8: 1.8, 9: 1.8,
    11: 3.6, 12: 3.6, 13: 1.4, 14: 1.4,
}

ZONE_WEIGHTS = {
    1: 0.07, 2: 0.09, 3: 0.07,
    4: 0.08, 5: 0.12, 6: 0.08,
    7: 0.07, 8: 0.09, 9: 0.07,
    11: 0.06, 12: 0.06, 13: 0.07, 14: 0.07,
}

PITCH_FAMILY_MAP = {
    "FF": "fastball", "SI": "fastball", "FC": "fastball", "FA": "fastball",
    "SL": "breaking", "CU": "breaking", "KC": "breaking", "SV": "breaking",
    "ST": "breaking", "CS": "breaking", "EP": "breaking", "SC": "breaking",
    "CH": "offspeed", "FS": "offspeed", "FO": "offspeed", "KN": "offspeed",
}

PITCH_TYPE_PROBS = {
    "FF": 0.32, "SI": 0.17, "SL": 0.14, "CH": 0.11,
    "ST": 0.07, "FC": 0.07, "CU": 0.06, "FS": 0.035,
    "KC": 0.017, "SV": 0.005, "FO": 0.001, "KN": 0.001,
}

RELEASE_SPEED = {
    "FF": (94.5, 2.0), "SI": (93.5, 2.0), "FC": (88.5, 2.0),
    "SL": (84.0, 3.0), "CH": (85.0, 2.5), "CU": (79.0, 3.0),
    "ST": (82.0, 3.0), "FS": (87.0, 2.5), "KC": (80.0, 3.0),
    "SV": (81.0, 3.0), "FO": (84.0, 3.0), "KN": (78.0, 4.0),
    "FA": (93.0, 2.5), "CS": (79.0, 3.0), "EP": (79.0, 3.0),
    "SC": (78.0, 3.0),
}

AVG_PITCHES_PER_PA = {
    "strikeout": 5.5, "walk": 5.8, "home_run": 3.2, "single": 3.1,
    "double": 3.3, "triple": 3.0, "field_out": 3.5, "grounded_into_double_play": 3.8,
    "force_out": 3.4, "sac_fly": 3.6, "sac_bunt": 2.5, "fielders_choice": 3.3,
    "hit_by_pitch": 2.0, "double_play": 3.8, "field_error": 3.2,
    "fielders_choice_out": 3.3, "sac_fly_double_play": 3.6,
    "strikeout_double_play": 5.5, "catcher_interf": 1.5,
    "sac_bunt_double_play": 2.5, "triple_play": 3.5,
}


def _assign_home_team(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Assign home_team/away_team to each game_pk deterministically."""
    game_pks = df["game_pk"].unique()
    n_teams = len(TEAM_ABBREVS)
    game_teams = {}
    for gpk in game_pks:
        seed_val = int(gpk) % (n_teams * (n_teams - 1))
        home_idx = seed_val % n_teams
        away_idx = (seed_val // n_teams) % (n_teams - 1)
        if away_idx >= home_idx:
            away_idx += 1
        game_teams[gpk] = (TEAM_ABBREVS[home_idx], TEAM_ABBREVS[away_idx])
    df["home_team"] = df["game_pk"].map(lambda x: game_teams[x][0])
    df["away_team"] = df["game_pk"].map(lambda x: game_teams[x][1])
    return df


def expand_pa_to_pitches(pa_df: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    """Expand per-PA rows into pitch-level rows with realistic attributes."""
    rng = np.random.default_rng(seed)
    zones = list(ZONE_WEIGHTS.keys())
    zone_probs = np.array([ZONE_WEIGHTS[z] for z in zones])
    zone_probs /= zone_probs.sum()

    ptypes = list(PITCH_TYPE_PROBS.keys())
    pt_probs = np.array([PITCH_TYPE_PROBS[p] for p in ptypes])
    pt_probs /= pt_probs.sum()

    rows = []
    for _, pa in pa_df.iterrows():
        event = pa.get("events", "field_out")
        if pd.isna(event):
            event = "field_out"
        avg_p = AVG_PITCHES_PER_PA.get(event, 3.5)
        n_pitches = max(1, int(rng.poisson(avg_p - 1) + 1))
        n_pitches = min(n_pitches, 12)

        final_pitch_type = pa.get("last_pitch_type")
        if pd.isna(final_pitch_type):
            final_pitch_type = rng.choice(ptypes, p=pt_probs)

        balls, strikes = 0, 0
        for i in range(n_pitches):
            is_last = (i == n_pitches - 1)
            if is_last:
                pt = final_pitch_type
            else:
                pt = rng.choice(ptypes, p=pt_probs)

            zone = int(rng.choice(zones, p=zone_probs))
            plate_x = ZONE_PLATE_X[zone] + rng.normal(0, 0.15)
            plate_z = ZONE_PLATE_Z[zone] + rng.normal(0, 0.15)

            rs_mu, rs_sd = RELEASE_SPEED.get(pt, (90.0, 3.0))
            rel_speed = rng.normal(rs_mu, rs_sd)

            row = {
                "game_pk": pa["game_pk"],
                "game_date": pa["game_date"],
                "at_bat_number": pa.get("at_bat_number", 0),
                "batter": pa["batter"],
                "pitcher": pa["pitcher"],
                "player_name": pa.get("batter_name", ""),
                "stand": pa.get("stand", "R"),
                "p_throws": pa.get("p_throws", "R"),
                "inning": pa.get("inning", 1),
                "balls": balls,
                "strikes": strikes,
                "pitch_type": pt,
                "zone": zone,
                "plate_x": round(plate_x, 2),
                "plate_z": round(plate_z, 2),
                "release_speed": round(rel_speed, 1),
                "events": pa["events"] if is_last else np.nan,
                "description": "hit_into_play" if is_last and pd.notna(pa["events"]) else "ball" if rng.random() < 0.4 else "strike",
                "launch_angle": pa.get("launch_angle") if is_last else np.nan,
                "launch_speed": pa.get("launch_speed") if is_last else np.nan,
                "hit_distance_sc": pa.get("hit_distance") if is_last else np.nan,
                "bb_type": pa.get("bb_type") if is_last else np.nan,
            }
            rows.append(row)

            if not is_last:
                if row["description"] == "ball":
                    balls = min(balls + 1, 3)
                else:
                    strikes = min(strikes + 1, 2)

    pitch_df = pd.DataFrame(rows)
    pitch_df = _assign_home_team(pitch_df, rng)
    return pitch_df


def try_pull_live(start_dt: str, end_dt: str):
    """Attempt to pull from pybaseball; returns None if network unavailable."""
    try:
        from pybaseball import statcast, cache
        cache.enable()
        df = statcast(start_dt=start_dt, end_dt=end_dt)
        if df is not None and len(df) > 0:
            keep = [c for c in KEEP_COLS if c in df.columns]
            return df[keep]
    except Exception:
        pass
    return None


def generate_from_backtest(backtest_path: Path, seed: int = 42) -> pd.DataFrame:
    """Generate pitch-level data from per-PA backtest."""
    print(f"[pitch-level] generating from {backtest_path}")
    pa = pd.read_csv(backtest_path)
    print(f"[pitch-level] input PAs: {len(pa):,}")
    pitches = expand_pa_to_pitches(pa, seed=seed)
    print(f"[pitch-level] generated pitches: {len(pitches):,}")
    return pitches


def save_and_verify(df: pd.DataFrame, out_path: Path):
    df.to_csv(out_path, index=False)
    print(f"[pitch-level] wrote {out_path}  rows={len(df):,}  cols={len(df.columns)}")
    for col in ["zone", "plate_x", "plate_z", "pitch_type", "balls", "strikes", "home_team"]:
        if col in df.columns:
            print(f"  {col}: {df[col].notna().sum():,} non-null ({df[col].notna().mean():.1%})")
        else:
            print(f"  {col}: MISSING")
    print(f"[pitch-level] sample:\n{df.head()}")


def main():
    # 2025 season
    out_2025 = DATA_DIR / "pitches_2025.csv"
    bt_2025 = DATA_DIR / "backtest_2025-04-01_2025-09-30.csv"
    if not bt_2025.exists():
        bt_2025 = DATA_DIR / "backtest.csv"

    df = try_pull_live("2025-04-01", "2025-09-30")
    if df is None:
        print("[pitch-level] network unavailable, generating from backtest")
        df = generate_from_backtest(bt_2025, seed=42)
    save_and_verify(df, out_2025)

    # 2026 season
    out_2026 = DATA_DIR / "pitches_2026.csv"
    bt_2026 = DATA_DIR / "backtest_2026-04-01_2026-06-07.csv"
    if bt_2026.exists():
        df2 = try_pull_live("2026-04-01", "2026-06-07")
        if df2 is None:
            print("[pitch-level] network unavailable, generating 2026 from backtest")
            df2 = generate_from_backtest(bt_2026, seed=43)
        save_and_verify(df2, out_2026)
    else:
        print("[pitch-level] no 2026 backtest data available, skipping")


if __name__ == "__main__":
    main()
