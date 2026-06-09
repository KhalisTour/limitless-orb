#!/usr/bin/env python3
"""Compute zone-level HR data from pitches_2025.csv (Tasks 4-6)."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
CSV_PATH = DATA_DIR / "pitches_2025.csv"

VALID_ZONES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14]

PITCH_FAMILIES = {
    "fastball": ["FF", "SI", "FC", "FA"],
    "breaking": ["SL", "CU", "KC", "SV", "ST", "CS", "EP", "SC"],
    "offspeed": ["CH", "FS", "FO", "KN"],
}
PITCH_TO_FAMILY = {}
for fam, codes in PITCH_FAMILIES.items():
    for c in codes:
        PITCH_TO_FAMILY[c] = fam


def compute_stats(df):
    """Compute zone stats for a dataframe of batted ball events."""
    n = len(df)
    if n == 0:
        return None
    hr_pct = float((df["events"] == "home_run").mean())
    avg_la = float(df["launch_angle"].mean())
    avg_ev = float(df["launch_speed"].mean())
    barrel_pct = float(
        ((df["launch_speed"] >= 98) & (df["launch_angle"] >= 26) & (df["launch_angle"] <= 30)).mean()
    )
    return {
        "hr_pct": round(hr_pct, 4),
        "avg_la": round(avg_la, 2),
        "avg_ev": round(avg_ev, 2),
        "barrel_pct": round(barrel_pct, 4),
        "n": n,
    }


def compute_trajectory(median_la, median_ev):
    """Compute trajectory arc calibrated to real Statcast distances.

    Uses a drag + lift model tuned so 105mph/28° ≈ 400ft, matching
    typical HR Statcast data. The lift term approximates Magnus backspin.
    """
    ev_fps = median_ev * 1.467
    la_rad = median_la * (math.pi / 180)
    vx = ev_fps * math.cos(la_rad)
    vy = ev_fps * math.sin(la_rad)
    g = 32.17
    drag_k = 0.0032
    lift_k = 0.0024
    dt_step = 0.05
    x_pos, y_pos = 0.0, 3.0
    trajectory = [[round(x_pos, 1), round(y_pos, 1)]]
    apex = y_pos
    for _ in range(2000):
        speed = math.sqrt(vx * vx + vy * vy)
        if speed < 1.0:
            break
        ax = -drag_k * speed * vx
        ay = -g - drag_k * speed * vy + lift_k * speed * speed
        vx += ax * dt_step
        vy += ay * dt_step
        x_pos += vx * dt_step
        y_pos += vy * dt_step
        trajectory.append([round(x_pos, 1), round(y_pos, 1)])
        if y_pos > apex:
            apex = y_pos
        if y_pos < 0:
            break
    distance = trajectory[-1][0]
    return {
        "median_la": round(median_la, 1),
        "median_ev": round(median_ev, 1),
        "apex_ft": round(apex, 1),
        "distance_ft": round(distance, 1),
        "trajectory": trajectory,
    }


def main():
    print("Loading data...")
    df = pd.read_csv(CSV_PATH)

    # --- Filter to batted ball events ---
    bbe = df[df["events"].notna() & df["launch_speed"].notna()].copy()
    # Filter to valid zones
    bbe = bbe[bbe["zone"].isin(VALID_ZONES)].copy()
    bbe["zone"] = bbe["zone"].astype(int)
    print(f"Batted ball events in valid zones: {len(bbe):,}")

    # Add pitch family
    bbe["pitch_family"] = bbe["pitch_type"].map(PITCH_TO_FAMILY)

    # ===== TASK 4: League-wide zone stats =====
    print("\n=== TASK 4: Zone-level HR probability grid ===")
    league_zone = {}
    for z in VALID_ZONES:
        zdf = bbe[bbe["zone"] == z]
        stats = compute_stats(zdf)
        if stats:
            league_zone[str(z)] = stats

    with open(DATA_DIR / "zone_hr_league.json", "w") as f:
        json.dump(league_zone, f, indent=2)

    print(f"League zone entries: {len(league_zone)} (expected 13)")
    for z in sorted(league_zone.keys(), key=lambda x: int(x)):
        s = league_zone[z]
        print(f"  Zone {z:>2}: hr_pct={s['hr_pct']:.4f}  avg_la={s['avg_la']:5.1f}  avg_ev={s['avg_ev']:5.1f}  barrel={s['barrel_pct']:.4f}  n={s['n']}")

    # --- Per-batter zone maps ---
    batter_bbe_counts = bbe.groupby("batter").size()
    qualified_batters = set(batter_bbe_counts[batter_bbe_counts >= 50].index)
    print(f"\nBatters with BBE >= 50: {len(qualified_batters)}")

    # Get batter names
    batter_names = bbe.groupby("batter")["player_name"].first().to_dict()

    zone_hr_maps = {}
    for batter_id in qualified_batters:
        bdf = bbe[bbe["batter"] == batter_id]
        zones_data = {}
        for z in VALID_ZONES:
            zdf = bdf[bdf["zone"] == z]
            stats = compute_stats(zdf)
            if stats is None:
                continue
            # Shrink hr_pct
            n = stats["n"]
            batter_rate = stats["hr_pct"]
            league_rate = league_zone[str(z)]["hr_pct"]
            shrunk = (n / (n + 30)) * batter_rate + (30 / (n + 30)) * league_rate
            stats["hr_pct"] = round(shrunk, 4)
            zones_data[str(z)] = stats
        zone_hr_maps[str(batter_id)] = {
            "name": batter_names.get(batter_id, "Unknown"),
            "zones": zones_data,
        }

    with open(DATA_DIR / "zone_hr_maps.json", "w") as f:
        json.dump(zone_hr_maps, f, indent=2)

    print(f"Batters in zone_hr_maps.json: {len(zone_hr_maps)} (expected >= 100)")

    # ===== TASK 5: Pitch-type-specific zone maps =====
    print("\n=== TASK 5: Pitch-type-specific zone maps ===")
    qualified_100 = set(batter_bbe_counts[batter_bbe_counts >= 100].index)
    print(f"Batters with BBE >= 100: {len(qualified_100)}")

    bbe_with_fam = bbe[bbe["pitch_family"].notna()]

    zone_hr_by_pitch = {}
    for batter_id in qualified_100:
        bdf = bbe_with_fam[bbe_with_fam["batter"] == batter_id]
        # Get batter's overall zone stats for shrinkage
        batter_overall = {}
        bdf_all = bbe[bbe["batter"] == batter_id]
        for z in VALID_ZONES:
            zdf = bdf_all[bdf_all["zone"] == z]
            s = compute_stats(zdf)
            if s:
                batter_overall[z] = s

        entry = {"name": batter_names.get(batter_id, "Unknown")}
        for fam in ["fastball", "breaking", "offspeed"]:
            fam_df = bdf[bdf["pitch_family"] == fam]
            fam_zones = {}
            for z in VALID_ZONES:
                zdf = fam_df[fam_df["zone"] == z]
                stats = compute_stats(zdf)
                if stats is None:
                    continue
                n = stats["n"]
                if n < 15 and z in batter_overall:
                    overall_rate = batter_overall[z]["hr_pct"]
                    stats["hr_pct"] = round(
                        (n / (n + 15)) * stats["hr_pct"] + (15 / (n + 15)) * overall_rate, 4
                    )
                fam_zones[str(z)] = stats
            entry[fam] = fam_zones
        zone_hr_by_pitch[str(batter_id)] = entry

    with open(DATA_DIR / "zone_hr_by_pitch.json", "w") as f:
        json.dump(zone_hr_by_pitch, f, indent=2)

    print(f"Batters in zone_hr_by_pitch.json: {len(zone_hr_by_pitch)} (expected >= 50)")

    # ===== TASK 6: Trajectory arcs =====
    print("\n=== TASK 6: Trajectory arcs ===")

    hr_df = bbe_with_fam[bbe_with_fam["events"] == "home_run"].copy()
    hr_df = hr_df[hr_df["launch_angle"].notna() & hr_df["launch_speed"].notna()]

    trajectory_arcs = {}
    arc_count = 0
    distances = []
    apexes = []

    for batter_id in qualified_100:
        bhr = hr_df[hr_df["batter"] == batter_id]
        if len(bhr) == 0:
            continue

        arcs = {}
        for fam in ["fastball", "breaking", "offspeed"]:
            for z in VALID_ZONES:
                subset = bhr[(bhr["zone"] == z) & (bhr["pitch_family"] == fam)]
                if len(subset) < 3:
                    continue

                median_la = float(subset["launch_angle"].median())
                median_ev = float(subset["launch_speed"].median())

                arc = compute_trajectory(median_la, median_ev)
                key = f"zone_{z}_{fam}"
                arcs[key] = arc
                arc_count += 1
                distances.append(arc["distance_ft"])
                apexes.append(arc["apex_ft"])

        if arcs:
            trajectory_arcs[str(batter_id)] = {
                "name": batter_names.get(batter_id, "Unknown"),
                "arcs": arcs,
            }

    with open(DATA_DIR / "trajectory_arcs.json", "w") as f:
        json.dump(trajectory_arcs, f, indent=2)

    print(f"Batters with arcs: {len(trajectory_arcs)}")
    print(f"Total arc entries: {arc_count}")
    if distances:
        print(f"Distance range: {min(distances):.0f} - {max(distances):.0f} ft (expected 300-500)")
        print(f"Apex range: {min(apexes):.0f} - {max(apexes):.0f} ft (expected 50-150)")

    print("\nDone! All files saved to data/")


if __name__ == "__main__":
    main()
