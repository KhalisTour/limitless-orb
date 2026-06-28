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
import re
import sys
import traceback
import unicodedata
import datetime as dt
from difflib import SequenceMatcher
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
    "p_throws": ["p_throws", "throws"],
}

BATTER_HAND_CANDIDATES = ["stand", "batter_hand", "bat_side"]


def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[.\-']", " ", name)
    return " ".join(name.lower().split())


def _flip_last_first(savant_name: str) -> str:
    if not isinstance(savant_name, str) or "," not in savant_name:
        return savant_name
    parts = savant_name.split(",", 1)
    return f"{parts[1].strip()} {parts[0].strip()}"


def _build_name_index(df: pd.DataFrame, name_col: str) -> dict:
    idx = {}
    for i, row in df.iterrows():
        raw = str(row[name_col])
        flipped = _flip_last_first(raw)
        norm = _normalize_name(flipped)
        idx[norm] = i
        norm_raw = _normalize_name(raw)
        if norm_raw != norm:
            idx[norm_raw] = i
    return idx


def _fuzzy_lookup(query: str, name_index: dict, threshold: float = 0.75) -> int | None:
    norm_q = _normalize_name(query)
    if norm_q in name_index:
        return name_index[norm_q]
    best_score, best_idx = 0.0, None
    for norm_name, row_idx in name_index.items():
        score = SequenceMatcher(None, norm_q, norm_name).ratio()
        if score > best_score:
            best_score, best_idx = score, row_idx
    if best_score >= threshold:
        return best_idx
    q_tokens = set(norm_q.split())
    for norm_name, row_idx in name_index.items():
        n_tokens = set(norm_name.split())
        if len(q_tokens) >= 2 and q_tokens.issubset(n_tokens) or n_tokens.issubset(q_tokens):
            return row_idx
    return None


def first_present(row: pd.Series, candidates) -> float:
    for c in candidates:
        if c in row.index and pd.notna(row[c]):
            return float(row[c])
    return None


def row_to_hitter_dict(row: pd.Series) -> dict:
    out = {k: first_present(row, cands) for k, cands in BATTER_KEY_CANDIDATES.items()}
    stand = first_present(row, BATTER_HAND_CANDIDATES)
    if stand is not None:
        out["stand"] = str(stand)
    return out


def row_to_pitcher_dict(row: pd.Series, arsenal: dict = None) -> dict:
    base = {k: first_present(row, cands) for k, cands in PITCHER_KEY_CANDIDATES.items()}
    base["arsenal"] = arsenal or _zero_arsenal()
    return base


def _zero_arsenal() -> dict:
    return dict(four_seam=0, sinker=0, cutter=0, slider=0,
                change=0, curve=0, split=0, kn=0)


def _row_to_arsenal(row: pd.Series) -> dict:
    """Map a Savant pitch-arsenals row (usage % per pitch) to models.py family keys.
    Sweeper (n_st) and slurve (n_sv) are breaking balls — folded into slider so the
    'soft' family captures their usage. Missing cells are 0% usage."""
    def g(col):
        if col in row.index and pd.notna(row[col]):
            try:
                return float(row[col])
            except (TypeError, ValueError):
                return 0.0
        return 0.0
    return dict(
        four_seam=g("n_ff"),
        sinker=g("n_si"),
        cutter=g("n_fc"),
        slider=g("n_sl") + g("n_st") + g("n_sv"),
        change=g("n_ch"),
        curve=g("n_cu"),
        split=g("n_fs"),
        kn=g("n_kn"),
    )


BATTER_DEFAULTS = {
    "barrel": 7.5, "xslg": 0.400, "hardhit": 40.0, "la": 12.5, "ev": 89.0,
    "whiff": 24.5, "k": 22.5, "bb": 8.5, "xwoba": 0.320, "xba": 0.250,
    "chase": 28.0, "swing": 47.0, "zone": 45.0, "zonesw": 65.0,
    "topped": 30.0, "under": 25.0, "flare": 22.0, "solid": 7.0, "weak": 4.0,
    "bbe": 150, "sprint": 27.0,
}

PITCHER_DEFAULTS = {
    "barrel": 7.5, "hardhit": 40.0, "xslg": 0.400, "xwoba": 0.320,
    "k": 22.5, "whiff": 24.5,
}

# Handedness-split priors. LHP misses more bats; RHP allows slightly more power on average.
RHP_PITCHER_DEFAULTS = {
    "barrel": 7.8, "hardhit": 40.5, "xslg": 0.406, "xwoba": 0.323,
    "k": 22.0, "whiff": 23.5,
}
LHP_PITCHER_DEFAULTS = {
    "barrel": 7.1, "hardhit": 39.3, "xslg": 0.392, "xwoba": 0.315,
    "k": 23.5, "whiff": 25.5,
}

_REGRESSION_K = 150  # PA weight of the prior; a pitcher with 150 PA is ~50/50 obs vs prior


def _get_pa_count(row: pd.Series) -> float:
    """Extract PA-faced count from a pitcher row; 0 if absent."""
    for c in ["pa", "total_pa", "b_total_pa", "abs"]:
        if c in row.index and pd.notna(row[c]):
            return float(row[c])
    return 0.0


def _pitcher_defaults_by_hand(throws) -> dict:
    if str(throws).strip().upper() == "L":
        return LHP_PITCHER_DEFAULTS
    return RHP_PITCHER_DEFAULTS


def _regress_pitcher(pdict: dict, n_pa: float, defaults: dict) -> dict:
    """Blend observed pitcher stats toward `defaults` weighted by sample size."""
    w = n_pa / (n_pa + _REGRESSION_K)
    result = dict(pdict)
    for key, prior_val in defaults.items():
        obs = pdict.get(key)
        result[key] = w * float(obs) + (1 - w) * prior_val if obs is not None else prior_val
    return result


def _fill_defaults(d: dict, defaults: dict):
    for k, v in defaults.items():
        if k in d and d[k] is None:
            d[k] = v


def find_id_col(df: pd.DataFrame, kind: str) -> str:
    for c in ["player_id", f"{kind}_id", "mlbam_id", "id"]:
        if c in df.columns:
            return c
        for col in df.columns:
            if col.lower() == c.lower():
                return col
    raise RuntimeError(f"No id column on {kind} CSV. Columns: {list(df.columns)[:20]}")


def find_name_col(df: pd.DataFrame) -> str:
    for c in ["player_name", "name", "full_name", "last_name, first_name"]:
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

    # Low-min board (min_pa=1): captures early-season / low-sample starters for regression fallback
    try:
        pit_allpa = fetch.get_pitcher_season(year=year, min_pa=1)
        pit_allpa.to_csv(out_dir / f"pitchers_{date}_allpa.csv", index=False)
        print(f"[ingest] pitchers_allpa={len(pit_allpa)}")
    except Exception as e:
        print(f"[ingest] low-min pitcher pull failed (continuing): {e}")

    # Pitcher handedness cache: Savant stat boards omit it, so look it up by id via
    # the Stats API. Handedness is static — only fetch ids not already cached.
    try:
        hands_path = DATA_DIR / "pitcher_hands.csv"
        hands_df = pd.read_csv(hands_path) if hands_path.exists() else \
            pd.DataFrame(columns=["player_id", "name", "throws"])
        have = set(pd.to_numeric(hands_df["player_id"], errors="coerce").dropna().astype(int))
        id_name = {}
        for board in (pit, locals().get("pit_allpa")):
            if board is None:
                continue
            nc = find_name_col(board)
            for _, r in board.iterrows():
                pid = r.get("player_id")
                if pd.notna(pid):
                    id_name.setdefault(int(pid), r.get(nc) if nc else None)
        missing = [pid for pid in id_name if pid not in have]
        if missing:
            fetched = fetch.get_pitcher_hands(missing)
            new_rows = [{"player_id": pid, "name": id_name.get(pid), "throws": thr}
                        for pid, thr in fetched.items()]
            if new_rows:
                hands_df = pd.concat([hands_df, pd.DataFrame(new_rows)], ignore_index=True)
                hands_df = hands_df.drop_duplicates(subset=["player_id"], keep="last")
                hands_df.to_csv(hands_path, index=False)
                print(f"[ingest] pitcher_hands: +{len(new_rows)} (total {len(hands_df)})")
    except Exception as e:
        print(f"[ingest] pitcher hands fetch failed (continuing): {e}")

    # Prior-year board: stable full-season reference; fetched once and cached in data/
    prev_year = year - 1
    prev_pit_path = DATA_DIR / f"pitchers_{prev_year}.csv"
    if not prev_pit_path.exists():
        try:
            prev_pit = fetch.get_pitcher_season(year=prev_year, min_pa=25)
            prev_pit.to_csv(prev_pit_path, index=False)
            print(f"[ingest] cached {prev_pit_path.name} ({len(prev_pit)} rows)")
        except Exception as e:
            print(f"[ingest] prior-year pitcher fetch failed (continuing): {e}")

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

    bat_index = _build_name_index(bat, name_col)
    pit_index = _build_name_index(pit, pname_col)

    HITTERS = {}
    LINEUP_ORDER = []
    skipped = []
    for _, row in my_lineup.iterrows():
        nm = row["player_name"]
        row_idx = _fuzzy_lookup(nm, bat_index)
        if row_idx is None:
            print(f"[bridge] WARN no batter row for {nm} — skipping")
            skipped.append(nm)
            continue
        hdict = row_to_hitter_dict(bat.loc[row_idx])
        _fill_defaults(hdict, BATTER_DEFAULTS)
        HITTERS[nm] = hdict
        LINEUP_ORDER.append(nm)

    total = len(my_lineup)
    if total > 0 and len(skipped) / total > 0.5:
        print(f"[bridge] WARNING: skipped {len(skipped)}/{total} batters: {skipped}")

    # Opposing SP — four-level cascade so we always have something real
    # Pre-load fallback boards once; they're small CSVs
    _allpa_path = snapshot_dir / f"pitchers_{date}_allpa.csv"
    _pit_allpa = pd.read_csv(_allpa_path) if _allpa_path.exists() else None
    _allpa_nc = find_name_col(_pit_allpa) if _pit_allpa is not None else None
    _allpa_index = _build_name_index(_pit_allpa, _allpa_nc) if _allpa_nc else {}

    _prev_year = int(date.split("-")[0]) - 1
    _prev_pit_path = DATA_DIR / f"pitchers_{_prev_year}.csv"
    _pit_prev = pd.read_csv(_prev_pit_path) if _prev_pit_path.exists() else None
    if _pit_prev is None:
        print(f"[bridge] prior-year board missing ({_prev_pit_path.name}) — Level 3 unavailable; run ingest_live.py to cache it")
    _prev_nc = find_name_col(_pit_prev) if _pit_prev is not None else None
    _prev_index = _build_name_index(_pit_prev, _prev_nc) if _prev_nc else {}

    # Arsenal board (pitch-usage %): drives the matchup model. Loaded here so the
    # resolved pitcher's pitch mix actually reaches models.py instead of all-zeros.
    _ars_path = snapshot_dir / f"arsenals_{date}.csv"
    _ars = pd.read_csv(_ars_path) if _ars_path.exists() else None
    if _ars is None:
        print(f"[bridge] arsenal board missing ({_ars_path.name}) — matchup model will be inert; run ingest_live.py")
    _ars_nc = find_name_col(_ars) if _ars is not None else None
    _ars_index = _build_name_index(_ars, _ars_nc) if _ars_nc else {}

    # Pitcher handedness (stat boards omit it) — resolve once up front so both the
    # cascade's handedness-aware priors and the platoon factor use the real hand.
    sp_throws = None
    _hands_path = DATA_DIR / "pitcher_hands.csv"
    if _hands_path.exists():
        _hands = pd.read_csv(_hands_path)
        if "throws" in _hands.columns:
            _hnc = find_name_col(_hands)
            _h_idx = _fuzzy_lookup(opp_sp_name, _build_name_index(_hands, _hnc)) if _hnc else None
            if _h_idx is not None:
                _thr = str(_hands.loc[_h_idx, "throws"] or "").strip().upper()
                sp_throws = _thr if _thr in ("L", "R") else None

    p_idx = _fuzzy_lookup(opp_sp_name, pit_index)
    if p_idx is not None:
        # Level 1: primary board (≥50 PA, current year) — normal path
        pdict = row_to_pitcher_dict(pit.loc[p_idx])
    else:
        # Look up both fallback boards before deciding what to blend
        _cur_row = None
        if _pit_allpa is not None:
            p_idx2 = _fuzzy_lookup(opp_sp_name, _allpa_index)
            if p_idx2 is not None:
                _cur_row = _pit_allpa.loc[p_idx2]

        _prev_row = None
        if _pit_prev is not None:
            p_idx3 = _fuzzy_lookup(opp_sp_name, _prev_index)
            if p_idx3 is not None:
                _prev_row = _pit_prev.loc[p_idx3]

        if _cur_row is not None:
            n_pa = _get_pa_count(_cur_row)
            throws = sp_throws
            if n_pa >= 50:
                # Enough current-year sample — use at face value
                pdict = row_to_pitcher_dict(_cur_row)
                print(f"[bridge] pitcher {opp_sp_name}: 2026 allpa ({n_pa:.0f} PA, full stats)")
            elif _prev_row is not None:
                # Low current-year sample — blend toward 2025 stats as the informed prior
                cur_dict = row_to_pitcher_dict(_cur_row)
                prev_dict = row_to_pitcher_dict(_prev_row)
                _fill_defaults(prev_dict, PITCHER_DEFAULTS)
                prior_2025 = {k: prev_dict[k] for k in PITCHER_DEFAULTS if prev_dict.get(k) is not None}
                pdict = _regress_pitcher(cur_dict, n_pa, prior_2025)
                print(f"[bridge] pitcher {opp_sp_name}: low-sample 2026 ({n_pa:.0f} PA, blended with {_prev_year} stats)")
            else:
                # Low current-year sample, no prior-year data — regress toward handedness prior
                defaults = _pitcher_defaults_by_hand(throws)
                pdict = _regress_pitcher(row_to_pitcher_dict(_cur_row), n_pa, defaults)
                hand_label = "LHP" if throws == "L" else "RHP"
                print(f"[bridge] pitcher {opp_sp_name}: low-sample 2026 ({n_pa:.0f} PA, regressed → {hand_label} prior, no {_prev_year} data)")
        elif _prev_row is not None:
            # No 2026 appearances at all — use prior-year full-season stats
            pdict = row_to_pitcher_dict(_prev_row)
            print(f"[bridge] pitcher {opp_sp_name}: {_prev_year} stats (no 2026 data)")
        else:
            # No data anywhere — handedness-aware league-average floor
            defaults = _pitcher_defaults_by_hand(sp_throws)
            pdict = dict(defaults)
            pdict["arsenal"] = _zero_arsenal()
            hand_label = "LHP" if sp_throws == "L" else ("RHP" if sp_throws else "RHP/unknown")
            print(f"[bridge] pitcher {opp_sp_name}: {hand_label} league-average floor (no data found)")

    # Record handedness so the platoon factor in predict_today uses the real hand.
    if sp_throws in ("L", "R"):
        pdict["p_throws"] = sp_throws

    # Attach the resolved pitcher's real arsenal so the matchup model has signal.
    a_idx = _fuzzy_lookup(opp_sp_name, _ars_index) if _ars_index else None
    if a_idx is not None:
        pdict["arsenal"] = _row_to_arsenal(_ars.loc[a_idx])
    elif not any(pdict.get("arsenal", {}).values()):
        print(f"[bridge] pitcher {opp_sp_name}: no arsenal match — matchup inert for this start")

    _fill_defaults(pdict, PITCHER_DEFAULTS)
    PITCHER = pdict
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
