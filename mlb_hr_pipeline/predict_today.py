"""
Run the ensemble + sim across today's slate, write predictions JSON.

For each game in the slate, for each side that has a posted lineup AND a
probable opposing starter, swap the live HITTERS/PITCHER/LINEUP into models.py's
module-level state, run model5_ensemble per hitter, and record per-PA HR
probabilities. Optionally run sim.simulate for distributional outputs.

Output: ./data/predictions_<date>.json
"""

import json
import sys
import traceback
import datetime as dt
from pathlib import Path

import pandas as pd

import fetch
import ingest_live


REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
MODELS_OUT = REPO / "models_out"


def _load_park_factors():
    p = DATA_DIR / "park_factors.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def _load_platoon_factors():
    p = DATA_DIR / "platoon_factors.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


TEAM_NAME_TO_ABBREV = {
    "Arizona Diamondbacks": "AZ", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}


def _apply_fitted_coefs():
    """If fit_model.py has written coefficients, push them into models.M3."""
    p = MODELS_OUT / "fitted_coefficients.json"
    if not p.exists():
        return False
    fit = json.loads(p.read_text())
    import models
    # Map fitted feature names to M3 keys where they overlap.
    overlap = {"barrel", "xslg", "hardhit", "la", "whiff"}
    for k in overlap:
        if k in fit["coefficients"]:
            models.M3[k] = float(fit["coefficients"][k])
    models.M3["intercept"] = float(fit["intercept"])
    # Once fitted, no need to damp.
    models.CALIBRATION_DAMP = 1.0
    print(f"[predict] applied fitted M3 coefficients from {p}")
    return True


def _apply_fitted_coefs_to(module, json_name: str,
                           overlap=("barrel", "xslg", "hardhit", "la", "whiff")):
    """Push fitted M3 coefficients into a model module (models_xbh / models_hit)."""
    p = MODELS_OUT / json_name
    if not p.exists():
        print(f"[predict] {json_name} not found — using reasoned {module.__name__} priors")
        return False
    fit = json.loads(p.read_text())
    for k in overlap:
        if k in fit.get("coefficients", {}):
            module.M3[k] = float(fit["coefficients"][k])
    module.M3["intercept"] = float(fit["intercept"])
    module.CALIBRATION_DAMP = 1.0
    print(f"[predict] applied fitted coefficients from {json_name}")
    return True


def _load_tensor():
    p = MODELS_OUT / "tensor_factors.json"
    if not p.exists():
        print("[predict] tensor_factors.json not found — TB/XBH tensor delta = 0; "
              "run fit_tensor.py to enable the matchup ranker")
        return None
    tf = json.loads(p.read_text())
    print(f"[predict] loaded tensor ({tf['meta']['n_batters']} batters, rank {tf['meta']['rank']})")
    return tf


def _tensor_deltas(tf, batter_id, arsenal):
    """Matchup delta (tb, xbh) for this batter vs this starter's pitch mix.

    delta = E[stat | batter, this arsenal] - E[stat | batter, league mix].
    The level is supplied by the logistic; this is purely the interaction term.
    Returns (0, 0) when the batter isn't in the tensor or the arsenal is empty.
    """
    if tf is None or batter_id is None:
        return 0.0, 0.0
    key = str(batter_id)
    etb = tf["etb"].get(key)
    exbh = tf["exbh"].get(key)
    if etb is None or exbh is None:
        return 0.0, 0.0
    fams = tf["families"]
    usage = [float(arsenal.get(f, 0) or 0) for f in fams]
    s = sum(usage)
    if s <= 0:
        return 0.0, 0.0
    usage = [u / s for u in usage]
    league = tf["league_usage"]
    tb_d = sum(e * u for e, u in zip(etb, usage)) - sum(e * l for e, l in zip(etb, league))
    xbh_d = sum(e * u for e, u in zip(exbh, usage)) - sum(e * l for e, l in zip(exbh, league))
    return tb_d, xbh_d


def _inject_state(state: dict):
    import models, sim
    models.HITTERS = state["HITTERS"]
    models.PITCHER = state["PITCHER"]
    models.LEAGUE  = state["LEAGUE"]
    models.PITCH_FAMILIES = state["PITCH_FAMILIES"]
    sim.HITTERS = state["HITTERS"]
    sim.LINEUP_ORDER = state["LINEUP_ORDER"]


def predict_side(state: dict, n_sims: int = 1000,
                  park_factor: float = 1.0, platoon_factors: dict = None,
                  pitcher_throws: str = None, tensor: dict = None) -> dict:
    _inject_state(state)
    import models, models_xbh, models_hit, sim
    # XBH/hit ensembles read module-level state too; share the same arsenal-bearing
    # PITCHER so their matchup terms are live (same fix as the HR path).
    for mod in (models_xbh, models_hit):
        mod.HITTERS = state["HITTERS"]
        mod.PITCHER = state["PITCHER"]
        mod.LEAGUE = state["LEAGUE"]
        mod.PITCH_FAMILIES = state["PITCH_FAMILIES"]
    arsenal = state["PITCHER"].get("arsenal", {})
    pf_map = platoon_factors or {}
    per_hitter = {}
    for name in state["LINEUP_ORDER"]:
        try:
            h = state["HITTERS"].get(name, {})
            stand = h.get("stand", "R") if isinstance(h.get("stand"), str) else "R"
            pt = pitcher_throws or "R"
            plat_key = f"{stand}_vs_{pt}"
            plat_f = pf_map.get(plat_key, 1.0)
            r = models.model5_ensemble(name, state["LINEUP_ORDER"],
                                       park_factor=park_factor,
                                       platoon_factor=plat_f)
            entry = {"p_per_pa": r["p_per_pa"], "exp_pa": r["exp_pa"],
                     "components": r["components"], "tto_mult": r["tto_mult"],
                     "park_factor": r["park_factor"],
                     "platoon_factor": r["platoon_factor"]}

            # XBH and TB: calibrated logistic level + tensor matchup delta.
            p_hr = r["p_per_pa"]
            p_xbh = models_xbh.model5_ensemble(name, state["LINEUP_ORDER"])["p_per_pa"]
            p_hit = models_hit.model5_ensemble(name, state["LINEUP_ORDER"])["p_per_pa"]
            tb_level = p_hit + p_xbh + 2.0 * p_hr   # E[TB]/PA ~ P(hit)+P(XBH)+2*P(HR)
            tb_d, xbh_d = _tensor_deltas(tensor, h.get("batter_id"), arsenal)
            exp_pa = r["exp_pa"]
            xbh_pa = max(0.0, p_xbh + xbh_d)
            tb_pa = max(0.0, tb_level + tb_d)
            entry["xbh"] = {"per_pa_level": p_xbh, "tensor_delta": xbh_d,
                            "per_pa": xbh_pa, "exp_per_game": xbh_pa * exp_pa}
            entry["tb"] = {"per_pa_level": tb_level, "tensor_delta": tb_d,
                           "per_pa": tb_pa, "exp_per_game": tb_pa * exp_pa}
            per_hitter[name] = entry
        except Exception as e:
            per_hitter[name] = {"error": str(e)}
    sim_summary = None
    if n_sims > 0:
        try:
            res = sim.simulate(n_games=n_sims)
            sim_summary = {
                "n_games": res["n_games"],
                "team_hr_dist": {str(k): v / res["n_games"] for k, v in res["team_hr_dist"].items()},
                "p_at_least_one_hr": {p: res["games_with_hr"][p] / res["n_games"]
                                      for p in state["LINEUP_ORDER"]},
                "p_multi_hr": {p: res["multi_hr_games"][p] / res["n_games"]
                               for p in state["LINEUP_ORDER"]},
                "back_to_back_per_game": res["back_to_back"] / res["n_games"],
            }
        except Exception as e:
            sim_summary = {"error": str(e)}
    return {"per_hitter": per_hitter, "sim": sim_summary, "pitcher": state["PITCHER"]["name"]}


def main(date: str = None):
    date = date or dt.date.today().isoformat()
    snap = ingest_live.SNAP_DIR / date
    if not snap.exists():
        print(f"[predict] no snapshot for {date}; running ingest first")
        snap = ingest_live.snapshot(date)
    slate = json.loads((snap / "slate.json").read_text())

    _apply_fitted_coefs()
    import models_xbh, models_hit
    _apply_fitted_coefs_to(models_xbh, "fitted_coefficients_xbh.json")
    _apply_fitted_coefs_to(models_hit, "fitted_coefficients_hit.json")
    tensor = _load_tensor()

    park_factors = _load_park_factors()
    platoon_factors_map = _load_platoon_factors()

    out = {"date": date, "generated_at": dt.datetime.utcnow().isoformat() + "Z", "games": []}
    for g in slate:
        home_team_full = g.get("home", "")
        home_abbrev = TEAM_NAME_TO_ABBREV.get(home_team_full, "")
        pf = park_factors.get(home_abbrev, 1.0)

        game_rec = {
            "game_id": g["game_id"],
            "away": g["away"], "home": g["home"],
            "away_sp": g.get("away_sp"), "home_sp": g.get("home_sp"),
            "game_datetime": g.get("game_datetime"),
            "venue": g.get("venue"),
            "park_factor": pf,
            "sides": {},
        }
        for side in ("away", "home"):
            try:
                opp = "home" if side == "away" else "away"
                opp_sp = g.get(f"{opp}_sp", "")
                state = ingest_live.build_inputs_for_game(g, snap, side=side)
                p_throws = state["PITCHER"].get("p_throws", "R")
                if not isinstance(p_throws, str) or p_throws not in ("L", "R"):
                    p_throws = "R"
                game_rec["sides"][side] = predict_side(
                    state, park_factor=pf,
                    platoon_factors=platoon_factors_map,
                    pitcher_throws=p_throws, tensor=tensor)
            except Exception as e:
                game_rec["sides"][side] = {"error": str(e)}
        out["games"].append(game_rec)

    try:
        from explain import explain_prediction
        for g in out["games"]:
            home_abbrev_g = TEAM_NAME_TO_ABBREV.get(g.get("home", ""), "")
            pf_g = park_factors.get(home_abbrev_g, 1.0)
            for side in ("away", "home"):
                sb = g["sides"].get(side, {})
                if not isinstance(sb, dict) or sb.get("error"):
                    continue
                for hitter, entry in sb.get("per_hitter", {}).items():
                    if "error" in entry:
                        continue
                    try:
                        plat_f = entry.get("platoon_factor", 1.0)
                        ex = explain_prediction(hitter, list(sb["per_hitter"].keys()),
                                                park_factor=pf_g, platoon_factor=plat_f)
                        entry["explanation"] = ex
                    except Exception:
                        pass
    except ImportError:
        print("[predict] explain.py not available, skipping explanations")

    out_path = DATA_DIR / f"predictions_{date}.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"[predict] wrote {out_path}  games={len(out['games'])}")
    return out_path


if __name__ == "__main__":
    try:
        d = sys.argv[1] if len(sys.argv) > 1 else None
        main(d)
    except Exception:
        traceback.print_exc()
        print("\nRerun: python predict_today.py [YYYY-MM-DD]", file=sys.stderr)
        sys.exit(1)
