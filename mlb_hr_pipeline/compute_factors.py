"""Compute park factors, platoon factors, and count factors from existing data."""

import json
import pandas as pd

DATA_DIR = "data"


def compute_park_factors():
    """Task 1: Park factors from pitches_2025.csv."""
    df = pd.read_csv(f"{DATA_DIR}/pitches_2025.csv")
    pa = df[df["events"].notna()].copy()

    league_hr_rate = (pa["events"] == "home_run").mean()

    park_stats = pa.groupby("home_team")["events"].agg(
        total="count",
        hrs=lambda x: (x == "home_run").sum(),
    )
    park_stats["hr_rate"] = park_stats["hrs"] / park_stats["total"]
    park_stats["park_factor"] = park_stats["hr_rate"] / league_hr_rate

    result = park_stats["park_factor"].round(4).to_dict()
    result = dict(sorted(result.items()))

    out_path = f"{DATA_DIR}/park_factors.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print("=== Park Factors ===")
    print(f"  League HR rate: {league_hr_rate:.5f}")
    print(f"  Teams: {len(result)}")
    vals = list(result.values())
    print(f"  Range: {min(vals):.4f} - {max(vals):.4f}")
    assert len(result) == 30, f"Expected 30 teams, got {len(result)}"
    assert all(0.5 <= v <= 2.0 for v in vals), "Values outside [0.5, 2.0]"
    print(f"  Saved to {out_path}")
    for team, val in result.items():
        print(f"    {team}: {val}")
    print()


def compute_platoon_factors():
    """Task 2: Platoon factors from backtest.csv."""
    df = pd.read_csv(f"{DATA_DIR}/backtest.csv")

    overall_hr_rate = df["hr"].mean()

    group = df.groupby(["stand", "p_throws"])["hr"].mean()

    result = {}
    for (stand, p_throws), hr_rate in group.items():
        key = f"{stand}_vs_{p_throws}"
        result[key] = round(hr_rate / overall_hr_rate, 4)

    result = dict(sorted(result.items()))

    out_path = f"{DATA_DIR}/platoon_factors.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print("=== Platoon Factors ===")
    print(f"  Overall HR rate: {overall_hr_rate:.5f}")
    print(f"  Entries: {len(result)}")
    vals = list(result.values())
    print(f"  Range: {min(vals):.4f} - {max(vals):.4f}")
    assert len(result) == 4, f"Expected 4 entries, got {len(result)}"
    assert all(0.7 <= v <= 1.5 for v in vals), "Values outside [0.7, 1.5]"
    print(f"  Saved to {out_path}")
    for key, val in result.items():
        print(f"    {key}: {val}")
    print()


def compute_count_factors():
    """Task 3: Count factors from pitches_2025.csv."""
    df = pd.read_csv(f"{DATA_DIR}/pitches_2025.csv")
    pa = df[df["events"].notna()].copy()

    overall_hr_rate = (pa["events"] == "home_run").mean()

    group = pa.groupby(["balls", "strikes"]).agg(
        total=("events", "count"),
        hrs=("events", lambda x: (x == "home_run").sum()),
    )
    group["hr_rate"] = group["hrs"] / group["total"]
    group["count_factor"] = group["hr_rate"] / overall_hr_rate

    result = {}
    for (balls, strikes), row in group.iterrows():
        key = f"{int(balls)}-{int(strikes)}"
        result[key] = round(row["count_factor"], 4)

    result = dict(sorted(result.items(), key=lambda x: (int(x[0].split("-")[0]), int(x[0].split("-")[1]))))

    out_path = f"{DATA_DIR}/count_factors.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print("=== Count Factors ===")
    print(f"  Overall HR rate: {overall_hr_rate:.5f}")
    print(f"  Entries: {len(result)}")
    vals = list(result.values())
    print(f"  Range: {min(vals):.4f} - {max(vals):.4f}")
    assert len(result) == 12, f"Expected 12 entries, got {len(result)}"
    assert all(0.2 <= v <= 2.5 for v in vals), "Values outside [0.2, 2.5]"
    print(f"  Saved to {out_path}")
    for key, val in result.items():
        print(f"    {key}: {val}")
    print()


if __name__ == "__main__":
    compute_park_factors()
    compute_platoon_factors()
    compute_count_factors()
    print("All factors computed and verified successfully.")
