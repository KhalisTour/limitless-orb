"""
sim.py — Monte Carlo full-game / lineup simulation.

Frames the question the way you asked: simulate a full game's worth of plate
appearances for the whole lineup, N times, and ask which HR outcomes co-occur
or cluster (back-to-back, multi-HR innings, same player going deep twice, etc.).

Each simulated game:
  - walks the lineup in order, dealing out PAs until ~38 PAs are used (a typical
    team-game), looping the order
  - each PA is a Bernoulli draw on that hitter's per-PA HR probability from Model 5
  - records WHO homered and in WHAT order, so proximity/streak stats are derivable
"""

import random
from collections import Counter, defaultdict
from data import HITTERS, LINEUP_ORDER
from models import model5_ensemble

TEAM_PA_PER_GAME = 38   # league-ish team total PAs in a 9-inning game

def _per_pa_probs():
    return {n: model5_ensemble(n, LINEUP_ORDER)["p_per_pa"] for n in HITTERS}

def simulate(n_games=1000, seed=7):
    rng = random.Random(seed)
    probs = _per_pa_probs()

    total_hr = Counter()             # HRs per player across all sims
    games_with_hr = Counter()        # games in which player homered >=1
    multi_hr_games = Counter()       # games player homered >=2
    back_to_back = 0                 # consecutive PAs (adjacent in order) both HR
    same_inning_multi = 0            # >=2 HR within a rolling 9-PA window (proxy inning cluster)
    team_hr_dist = Counter()         # distribution of total team HR per game

    for _ in range(n_games):
        order_idx = 0
        pa_count = 0
        game_hr = Counter()
        hr_sequence = []  # list of (pa_index, player) for HRs this game
        last_was_hr = False

        while pa_count < TEAM_PA_PER_GAME:
            batter = LINEUP_ORDER[order_idx % len(LINEUP_ORDER)]
            is_hr = rng.random() < probs[batter]
            if is_hr:
                game_hr[batter] += 1
                hr_sequence.append((pa_count, batter))
                if last_was_hr:
                    back_to_back += 1
            last_was_hr = is_hr
            pa_count += 1
            order_idx += 1

        # tally
        for p, c in game_hr.items():
            total_hr[p] += c
            games_with_hr[p] += 1
            if c >= 2:
                multi_hr_games[p] += 1
        team_hr_dist[sum(game_hr.values())] += 1

        # proximity: any two HRs within 9 PAs of each other (rough "same inning-ish")
        idxs = [pa for pa, _ in hr_sequence]
        for i in range(len(idxs)):
            for j in range(i + 1, len(idxs)):
                if idxs[j] - idxs[i] <= 9:
                    same_inning_multi += 1
                    break

    return dict(
        n_games=n_games, probs=probs, total_hr=total_hr,
        games_with_hr=games_with_hr, multi_hr_games=multi_hr_games,
        back_to_back=back_to_back, same_inning_multi=same_inning_multi,
        team_hr_dist=team_hr_dist,
    )

def report(res):
    n = res["n_games"]
    print(f"\n=== Monte Carlo: {n} simulated games vs {__import__('data').PITCHER['name']} ===\n")
    print(f"{'Hitter':<18}{'P(HR)/PA':>9}{'HR/game':>9}{'P(>=1 HR)':>11}{'P(multi)':>10}")
    print("-" * 57)
    # rank by games_with_hr (probability of going deep in a given game)
    ranked = sorted(HITTERS, key=lambda p: res["games_with_hr"][p], reverse=True)
    for p in ranked:
        ppa = res["probs"][p]
        hr_per_g = res["total_hr"][p] / n
        p1 = res["games_with_hr"][p] / n
        pm = res["multi_hr_games"][p] / n
        print(f"{p:<18}{ppa:>9.3f}{hr_per_g:>9.3f}{p1:>11.3f}{pm:>10.3f}")

    print("\n--- Co-occurrence / proximity outcomes ---")
    print(f"Back-to-back HR (adjacent PAs):   {res['back_to_back']/n:.3f} per game")
    print(f"Two HR within 9 PAs (cluster):    {res['same_inning_multi']/n:.3f} per game")
    print("\nTeam HR per game distribution:")
    for k in sorted(res["team_hr_dist"]):
        bar = "#" * round(40 * res["team_hr_dist"][k] / n)
        print(f"  {k} HR: {res['team_hr_dist'][k]/n:>5.3f} {bar}")

if __name__ == "__main__":
    report(simulate(1000))
