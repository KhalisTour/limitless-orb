"""
Phase 2 — Live ingestion.

For today's slate (or a provided date), pull:
  - Game schedule + probable starters (MLB Stats API)
  - Posted lineups when available (MLB Stats API boxscore)
  - Season batter table (Savant)
  - Season pitcher table (Savant)

Write daily snapshot CSVs under ./data/snapshots/<date>/:
  - batters_<date>.csv
  - pitchers_<date>.csv
  - lineup.csv (one row per (game, side, slot, player_name, player_id))
  - slate.json (list of games with probable SPs)

Also implements build_inputs_for_game(game, ...) which returns the
(HITTERS, PITCHER, LINEUP_ORDER) dicts in the shape models.py expects.
The column-name mapping happens here, in one place, so models.py stays clean.
"""

import io
import json
import sys
import traceback
import datetime as dt
from pathlib import Path

import pandas as pd

import fetch  # local module


REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
SNAP_DIR = DATA_DIR / "snapshots"
SNAP_DIR.mkdir(parents=True, exist_ok=True)


# Map Savant batter columns -> models.py HITTERS dict keys.
# Listed first-wins; None if no column resolves.
BATTER_KEY_CANDIDATES = {
    "barrel":  ["barrel_batted_rate"],
    "xslg":    ["xslg"],
    "hardhit": ["hard_hit_percent"],
    "la":      ["launch_angle_avg", "avg_launch_angle"],
    "ev":      ["exit_velocity_avg", "avg_best_speed", "avg_hyper_speed"],
    "whiff":   ["whiff_percent"],
    "k":       ["k_percent"],
    "bb":      ["bb_percent"],
    "xwoba":   ["xwoba"],
    "xba":     ["xba"],
    "chase":   ["oz_swing_percent", "out_zone_swing_percent", "chase_percent"],
    "swing":   ["swing_percent"],
    "zone":    ["zone_percent", "in_zone_percent"],
    "zonesw":  ["z_swing_percent", "in_zone_swing_percent"],
    "topped":  ["topped_percent"],
    "under":   ["under_percent"],
    "flare":   ["flare_burner_percent", "flare_percent"],
    "solid":   ["solid_contact_percent", "solid_percent"],
    "weak":    ["weak_percent"],
    "bbe":     ["batted_ball", "batted_balls", "abs"],
    "sprint":  ["sprint_speed"],
}

PITCHER_KEY_CANDIDATES = {
    "barrel":  ["barrel_batted_rate"],
    "hardhit": ["hard_hit_percent"],
    "xslg":    ["xslg"],
    "xwoba":   ["xwoba"],
    "k":       ["k_percent"],
    "whiff":   ["whiff_percent"],
}


def first_present(row: pd.Series, candidates) -> float:
    for c in candidates:
        if c in row.index and pd.notna(row[c]):
            return float(row[c])
    return None


def row_to_hitter_dict(row: pd.Series) -> dict:
    out = {k: first_present(row, cands) for k, cands in BATTER_KEY_CANDIDATES.items()}
    return out


def row_to_pitcher_dict(row: pd.Series, arsenal: dict = None) -> dict:
    base = {k: first_present(row, cands) for k, cands in PITCHER_KEY_CANDIDATES.items()}
    base["arsenal"] = arsenal or dict(
        four_seam=0, sinker=0, cutter=0, slider=0,
        change=0, curve=0, split=0, kn=0,
    )
    return base


def find_id_col(df: pd.DataFrame, kind: str) -> str:
    for c in ["player_id", f"{kind}_id", "mlbam_id", "id"]:
        if c in df.columns:
            return c
        for col in df.columns:
            if col.lower() == c.lower():
                return col
    raise RuntimeError(f"No id column on {kind} CSV. Columns: {list(df.columns)[:20]}")


def find_name_col(df: pd.DataFrame) -> str:
    for c in ["player_name", "name", "full_name"]:
        if c in df.columns:
            return c
        for col in df.columns:
            if col.lower() == c.lower():
                return col
    return None


def snapshot(date: str = None):
    date = date or dt.date.today().isoformat()
    out_dir = SNAP_DIR / date
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[ingest] snapshot for {date}")
    slate = fetch.get_slate(date)
    print(f"[ingest] games: {len(slate)}")
    (out_dir / "slate.json").write_text(json.dumps(slate, indent=2))

    year = int(date.split("-")[0])
    bat = fetch.get_batter_season(year=year)
    pit = fetch.get_pitcher_season(year=year)
    print(f"[ingest] batters={len(bat)} pitchers={len(pit)}")
    bat.to_csv(out_dir / f"batters_{date}.csv", index=False)
    pit.to_csv(out_dir / f"pitchers_{date}.csv", index=False)
    try:
        ars = fetch.get_pitch_arsenal(year=year)
        ars.to_csv(out_dir / f"arsenals_{date}.csv", index=False)
    except Exception as e:
        print(f"[ingest] arsenal pull failed (continuing): {e}")
        ars = None

    # Lineups (may be empty pre-lineup-post). Walk slate, swallow errors per game.
    lineups = []
    for g in slate:
        try:
            lu = fetch.get_lineup(g["game_id"])
            for side in ("away", "home"):
                for slot, name in enumerate(lu.get(side, []), start=1):
                    lineups.append(dict(game_id=g["game_id"], side=side,
                                        slot=slot, player_name=name))
        except Exception as e:
            print(f"[ingest] no lineup yet for {g['game_id']} ({g['away']} @ {g['home']}): {e}")
    if lineups:
        pd.DataFrame(lineups).to_csv(out_dir / "lineup.csv", index=False)
        print(f"[ingest] lineup rows: {len(lineups)}")
    else:
        print("[ingest] no posted lineups yet")

    print(f"[ingest] wrote snapshot dir {out_dir}")
    return out_dir


def build_inputs_for_game(game: dict, snapshot_dir: Path, side: str = "away") -> dict:
    """Return (HITTERS, PITCHER, LINEUP_ORDER, LEAGUE, PITCH_FAMILIES) for one
    side of one game. `side` is which side's HITTERS we want; the opposing
    pitcher is who they're facing.
    """
    date = snapshot_dir.name
    bat = pd.read_csv(snapshot_dir / f"batters_{date}.csv")
    pit = pd.read_csv(snapshot_dir / f"pitchers_{date}.csv")
    lineup_path = snapshot_dir / "lineup.csv"
    if not lineup_path.exists():
        raise RuntimeError(f"No lineup.csv in {snapshot_dir} — lineups likely not posted yet")
    lineup = pd.read_csv(lineup_path)

    opp = "home" if side == "away" else "away"
    opp_sp_name = game.get(f"{opp}_sp")
    if not opp_sp_name:
        raise RuntimeError(f"No probable pitcher for {opp} side of game {game.get('game_id')}")

    my_lineup = lineup[(lineup.game_id == game["game_id"]) & (lineup.side == side)].sort_values("slot")
    if my_lineup.empty:
        raise RuntimeError(f"No {side} lineup yet for game {game.get('game_id')}")

    name_col = find_name_col(bat)
    if name_col is None:
        raise RuntimeError("Batter CSV has no name column")
    pname_col = find_name_col(pit)

    HITTERS = {}
    LINEUP_ORDER = []
    for _, row in my_lineup.iterrows():
        nm = row["player_name"]
        match = bat[bat[name_col].astype(str).str.lower() == str(nm).lower()]
        if match.empty:
            print(f"[bridge] WARN no batter row for {nm} — skipping")
            continue
        HITTERS[nm] = row_to_hitter_dict(match.iloc[0])
        LINEUP_ORDER.append(nm)

    # Opposing SP
    p_match = pit[pit[pname_col].astype(str).str.lower() == str(opp_sp_name).lower()]
    if p_match.empty:
        raise RuntimeError(f"No pitcher row for {opp_sp_name}")
    PITCHER = row_to_pitcher_dict(p_match.iloc[0])
    PITCHER["name"] = opp_sp_name

    # League baselines from base_rates.json if present, else sensible defaults.
    base_rates_path = DATA_DIR / "base_rates.json"
    if base_rates_path.exists():
        br = json.loads(base_rates_path.read_text())
        league = dict(
            hr_per_pa=br.get("hr_per_pa", 0.032),
            barrel=7.5, hardhit=40.0, ev=89.0, la=12.5,
            xslg=0.400, k=22.5, whiff=24.5,
        )
    else:
        league = dict(hr_per_pa=0.032, barrel=7.5, hardhit=40.0, ev=89.0,
                      la=12.5, xslg=0.400, k=22.5, whiff=24.5)

    pitch_families = {
        "rise": ["four_seam", "cutter"],
        "sink": ["sinker", "split"],
        "soft": ["change", "curve", "slider", "kn"],
    }
    return dict(HITTERS=HITTERS, PITCHER=PITCHER, LINEUP_ORDER=LINEUP_ORDER,
                LEAGUE=league, PITCH_FAMILIES=pitch_families)


if __name__ == "__main__":
    try:
        d = sys.argv[1] if len(sys.argv) > 1 else None
        snapshot(d)
    except Exception:
        traceback.print_exc()
        print("\nRerun: python ingest_live.py [YYYY-MM-DD]", file=sys.stderr)
        sys.exit(1)
