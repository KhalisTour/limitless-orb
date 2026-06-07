"""
fetch.py — Data ingestion layer. Replaces the hand-typed data.py.

This is the piece you asked about ("how do I make the API"). Notice there is
NO server here and nothing to host. "Using an API" just means making HTTPS GET
requests to endpoints other people already run, then parsing what comes back.
A plain Python script on a schedule (cron / Task Scheduler / GitHub Action) is
all you need.

THREE DATA TAPS — each replaces a layer of the screenshot you were going to scrape:

  1. SLATE + PROBABLES + LINEUPS   -> MLB Stats API (official JSON, free)
  2. SEASON AGGREGATE PLAYER STATS -> Baseball Savant custom-leaderboard CSV
  3. PITCH-BY-PITCH (backtest)      -> pybaseball.statcast() (wraps Savant)

NOTE: This scaffold could not be run against the live endpoints from the
environment it was written in (network was sandboxed). Run it locally. The
endpoint shapes are verified against current docs but treat the column names
as "confirm on first run" — Savant occasionally renames columns.

Install:
    pip install pybaseball MLB-StatsAPI requests pandas
"""

import io
import datetime as dt
import requests
import pandas as pd

# pybaseball + statsapi are imported lazily inside functions so the file
# imports cleanly even if you only want one tap.


# ---------------------------------------------------------------------------
# TAP 1 — Today's slate: who's playing, who's starting, batting order
#   Source: MLB Stats API (statsapi.mlb.com). Official, free, JSON, no scraping.
#   This is what the Savant "Game Preview" page is itself built on top of.
# ---------------------------------------------------------------------------

def get_slate(date=None):
    """Return today's games with probable starters. date as 'YYYY-MM-DD'."""
    import statsapi
    date = date or dt.date.today().isoformat()
    sched = statsapi.schedule(date=date)          # list of dicts
    games = []
    for g in sched:
        games.append(dict(
            game_id=g["game_id"],
            away=g["away_name"], home=g["home_name"],
            away_sp=g.get("away_probable_pitcher") or None,
            home_sp=g.get("home_probable_pitcher") or None,
        ))
    return games


def get_lineup(game_id):
    """Return batting order (slot 1-9) for both teams once lineups are posted."""
    import statsapi
    box = statsapi.boxscore_data(game_id)
    out = {}
    for side in ("away", "home"):
        order = box[side]["battingOrder"]          # list of player IDs in slot order
        players = box[side]["players"]
        out[side] = [players[f"ID{pid}"]["person"]["fullName"] for pid in order]
    return out


# ---------------------------------------------------------------------------
# TAP 2 — Season aggregate stats (the leaderboard CSV from your screenshot)
#   Source: Savant custom leaderboard. Add &csv=true to the SAME url you saw in
#   the browser and it returns a CSV instead of HTML. No scraping, no parsing.
#   The `selections=` list is your column picker — request exactly the columns
#   the model needs (see FEATURES below).
# ---------------------------------------------------------------------------

BATTER_FEATURES = [
    "xba", "xslg", "xobp", "xiso", "xwoba",
    "avg_best_speed", "avg_hyper_speed",      # EV family
    "barrel_batted_rate", "hard_hit_percent",
    "launch_angle_avg", "sweet_spot_percent",
    "k_percent", "bb_percent",
    "pull_percent", "straightaway_percent", "opposite_percent",
    "whiff_percent", "swing_percent",
    "groundballs_percent", "flyballs_percent",
    "bat_speed", "attack_angle", "ideal_angle_rate",   # bat-tracking
]

def get_batter_season(year=None, min_pa="q"):
    """Pull season batter table as a DataFrame straight from Savant CSV."""
    year = year or dt.date.today().year
    sel = "%2C".join(BATTER_FEATURES)
    url = (f"https://baseballsavant.mlb.com/leaderboard/custom"
           f"?year={year}&type=batter&filter=&min={min_pa}"
           f"&selections={sel}&csv=true")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))


def get_pitcher_season(year=None, min_pa="q"):
    """Pitcher equivalent. type=pitcher; arsenal usage comes from a separate tap."""
    year = year or dt.date.today().year
    feats = ["xba", "xslg", "xwoba", "k_percent", "bb_percent",
             "barrel_batted_rate", "hard_hit_percent", "whiff_percent",
             "groundballs_percent", "flyballs_percent"]
    sel = "%2C".join(feats)
    url = (f"https://baseballsavant.mlb.com/leaderboard/custom"
           f"?year={year}&type=pitcher&filter=&min={min_pa}"
           f"&selections={sel}&csv=true")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))


def get_pitch_arsenal(year=None):
    """Pitch-mix % per pitcher (4-seam/sinker/cutter/etc). Separate Savant board."""
    year = year or dt.date.today().year
    url = (f"https://baseballsavant.mlb.com/leaderboard/pitch-arsenals"
           f"?year={year}&min=1&type=n_&hand=&csv=true")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))


# ---------------------------------------------------------------------------
# TAP 3 — Pitch-by-pitch for BACKTESTING + fitting the model
#   Source: pybaseball.statcast(start, end). One row PER PITCH. Every HR in the
#   range is rows where events == 'home_run', and each carries launch_angle,
#   launch_speed, hit_distance_sc, pitch_type, balls, strikes, inning, etc.
#   THIS is the dataset that turns Model 3's reasoned coefficients into fitted
#   ones (the integrity gap flagged in the MVP).
# ---------------------------------------------------------------------------

BACKTEST_COLS = [
    "game_date", "batter", "pitcher", "events", "description",
    "launch_angle", "launch_speed", "hit_distance_sc",
    "pitch_type", "release_speed", "effective_speed",
    "balls", "strikes", "inning", "plate_x", "plate_z", "zone",
    "stand", "p_throws", "bb_type",
]

def get_backtest(start_dt, end_dt):
    """Pull pitch-level data for a date range. Enable caching for big pulls."""
    from pybaseball import statcast
    from pybaseball import cache
    cache.enable()                                  # disk-cache so you pull once
    df = statcast(start_dt=start_dt, end_dt=end_dt)
    keep = [c for c in BACKTEST_COLS if c in df.columns]
    return df[keep]


def label_per_pa(df):
    """Collapse pitch rows to PER-PLATE-APPEARANCE rows with a HR (0/1) label.

    This is the training table for fitting Model 3 as a real logistic regression:
    one row per PA, y = did this PA end in a HR. Join season features onto the
    `batter`/`pitcher` IDs to build X.
    """
    # last pitch of each PA carries the terminal `events`
    df = df.sort_values(["game_date", "inning", "pitcher", "batter"])
    pa = (df.groupby(["game_date", "pitcher", "batter", "inning"], as_index=False)
            .agg(events=("events", "last")))
    pa["hr"] = (pa["events"] == "home_run").astype(int)
    return pa


# ---------------------------------------------------------------------------
# BRIDGE — assemble fetched data into the dict shape models.py already expects
#   (HITTERS = {name: {...}}, PITCHER = {...}). This is the only glue you need
#   to swap the hand-typed data.py for live data without touching models.py.
# ---------------------------------------------------------------------------

def build_inputs_for_game(game, year=None):
    """Return (HITTERS, PITCHER_for_each_side) ready to feed Model 5.
    Stub: wire get_batter_season + get_lineup + get_pitcher_season together,
    renaming Savant columns to the keys models.py uses (barrel, hardhit, ...).
    Left as a TODO so you can confirm exact CSV column names on first run."""
    raise NotImplementedError("Map CSV columns -> models.py keys here.")


if __name__ == "__main__":
    # Smoke test the slate tap (the only one that needs no heavy deps).
    for g in get_slate():
        print(f"{g['away']} @ {g['home']}: {g['away_sp']} vs {g['home_sp']}")
