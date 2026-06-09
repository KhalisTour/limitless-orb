#!/usr/bin/env python3
"""Compute pitcher zone tendency data from pitches_2025.csv."""

import csv
import json
from collections import defaultdict

INPUT_CSV = "data/pitches_2025.csv"
OUTPUT_JSON = "data/pitcher_zone_tendency.json"

VALID_ZONES = {str(z) for z in list(range(1, 10)) + list(range(11, 15))}

PITCH_FAMILIES = {
    "fastball": {"FF", "SI", "FC", "FA"},
    "breaking": {"SL", "CU", "KC", "SV", "ST", "CS", "EP", "SC"},
    "offspeed": {"CH", "FS", "FO", "KN"},
}

MIN_PITCHES = 200


def main():
    # Accumulate counts: pitcher -> zone -> count, and pitcher -> family -> zone -> count
    pitcher_total = defaultdict(int)
    pitcher_zone = defaultdict(lambda: defaultdict(int))
    pitcher_family_total = defaultdict(lambda: defaultdict(int))
    pitcher_family_zone = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    with open(INPUT_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row["pitcher"]
            zone = row["zone"]
            pitch_type = row["pitch_type"]

            pitcher_total[pid] += 1

            if zone in VALID_ZONES:
                pitcher_zone[pid][zone] += 1

            # Determine family
            for family, types in PITCH_FAMILIES.items():
                if pitch_type in types:
                    pitcher_family_total[pid][family] += 1
                    if zone in VALID_ZONES:
                        pitcher_family_zone[pid][family][zone] += 1
                    break

    # Build output for pitchers with >= MIN_PITCHES
    result = {}
    for pid, total in pitcher_total.items():
        if total < MIN_PITCHES:
            continue

        entry = {
            "name": f"pitcher_{pid}",
            "all": {},
        }

        # Overall zone %
        for z in VALID_ZONES:
            entry["all"][z] = round(pitcher_zone[pid].get(z, 0) / total, 4)

        # Per-family zone %
        for family in PITCH_FAMILIES:
            fam_total = pitcher_family_total[pid].get(family, 0)
            fam_zones = {}
            if fam_total > 0:
                for z in VALID_ZONES:
                    fam_zones[z] = round(
                        pitcher_family_zone[pid][family].get(z, 0) / fam_total, 4
                    )
            else:
                for z in VALID_ZONES:
                    fam_zones[z] = 0.0
            entry[family] = fam_zones

        result[pid] = entry

    with open(OUTPUT_JSON, "w") as f:
        json.dump(result, f, indent=2)

    # Verification
    print(f"Total pitchers in data: {len(pitcher_total)}")
    print(f"Pitchers with >= {MIN_PITCHES} pitches: {len(result)}")
    assert len(result) >= 100, f"Expected >= 100 pitchers, got {len(result)}"
    print("Verification PASSED: >= 100 pitchers")

    # Show a sample entry
    sample_pid = next(iter(result))
    sample = result[sample_pid]
    print(f"\nSample pitcher: {sample['name']}")
    print(f"  All zones: { {k: v for k, v in sorted(sample['all'].items(), key=lambda x: int(x[0]))} }")
    print(f"  Fastball zones (top 3): { dict(sorted(sample['fastball'].items(), key=lambda x: -x[1])[:3]) }")
    print(f"\nSaved to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
