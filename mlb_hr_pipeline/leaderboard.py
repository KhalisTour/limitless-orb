"""
leaderboard.py — print today's top HR predictions to the terminal.
Run from your project root with your venv active:
    python leaderboard.py
"""

import json
import glob
import os

# ── find the most recent predictions file ──────────────────────────────────
files = sorted(glob.glob("data/predictions_*.json"))
if not files:
    print("No predictions files found in data/")
    exit(1)
latest = files[-1]
d = json.load(open(latest))
date = d["date"]

# ── also load lineup for slot numbers ─────────────────────────────────────
lineup_path = f"data/snapshots/{date}/lineup.csv"
slots = {}  # (game_id, player_name) -> slot
if os.path.exists(lineup_path):
    import csv
    with open(lineup_path) as f:
        for row in csv.DictReader(f):
            slots[(int(row["game_id"]), row["player_name"])] = int(row["slot"])

# ── flatten all hitters from all games ────────────────────────────────────
picks = []
for game in d["games"]:
    gid = game["game_id"]
    for side in ["away", "home"]:
        s = game["sides"][side]
        if "per_hitter" not in s:
            continue  # lineup not yet posted or pitcher missing — skip
        opp_sp = game[f"{'home' if side == 'away' else 'away'}_sp"]
        opp_team = game["home" if side == "away" else "away"]
        for name, h in s["per_hitter"].items():
            slot = slots.get((gid, name), "?")
            sim = s["sim"].get("p_at_least_one_hr", {}).get(name, None)
            picks.append({
                "name": name,
                "opp_sp": opp_sp,
                "opp_team": opp_team,
                "game_id": gid,
                "slot": slot,
                "p_per_pa": h["p_per_pa"],
                "p_game": sim,
                "park_factor": h.get("park_factor", 1.0),
                "platoon_factor": h.get("platoon_factor", 1.0),
                "logistic": h["components"]["logistic"],
                "matchup": h["components"]["matchup"],
                "linear": h["components"]["linear"],
            })

# ── sort by per-PA probability ─────────────────────────────────────────────
picks.sort(key=lambda x: x["p_per_pa"], reverse=True)

# ── compute percentile ranks ───────────────────────────────────────────────
n = len(picks)
for i, p in enumerate(picks):
    p["pctile"] = round(100 * (n - i) / n)

# ── print leaderboard ──────────────────────────────────────────────────────
print(f"\n{'='*72}")
print(f"  HOME RUN LEADERBOARD — {date}  ({n} hitters across {len(d['games'])} games)")
print(f"{'='*72}")
print(f"{'#':<4}{'Hitter':<22}{'Opp SP':<20}{'Slot':>4}  {'P/PA':>6}  {'P(HR)':>6}  {'Pctile':>7}")
print(f"{'-'*72}")

for i, p in enumerate(picks[:30], 1):
    game_hr = f"{p['p_game']:.1%}" if p["p_game"] is not None else "  n/a"
    print(
        f"{i:<4}"
        f"{p['name']:<22}"
        f"{p['opp_sp']:<20}"
        f"{str(p['slot']):>4}  "
        f"{p['p_per_pa']:>6.1%}  "
        f"{game_hr:>6}  "
        f"{p['pctile']:>6}th"
    )

print(f"\n  P/PA  = probability of HR in a single plate appearance")
print(f"  P(HR) = probability of hitting at least 1 HR in this game (from sim)")
print(f"  Slot  = batting order position")
print(f"\n  Showing top 20 of {n} predicted hitters.")
sides_with_errors = sum(
    1 for g in d["games"] for side in ["away","home"]
    if "error" in g["sides"][side]
)
if sides_with_errors:
    print(f"  Note: {sides_with_errors} lineup slots missing (lineups not yet posted).")
print()
