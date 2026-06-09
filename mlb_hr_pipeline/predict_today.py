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


def _inject_state(state: dict):
    import models, sim
    models.HITTERS = state["HITTERS"]
    models.PITCHER = state["PITCHER"]
    models.LEAGUE  = state["LEAGUE"]
    models.PITCH_FAMILIES = state["PITCH_FAMILIES"]
    sim.HITTERS = state["HITTERS"]
    sim.LINEUP_ORDER = state["LINEUP_ORDER"]


def predict_side(state: dict, n_sims: int = 1000) -> dict:
    _inject_state(state)
    import models, sim
    per_hitter = {}
    for name in state["LINEUP_ORDER"]:
        try:
            r = models.model5_ensemble(name, state["LINEUP_ORDER"])
            per_hitter[name] = {"p_per_pa": r["p_per_pa"], "exp_pa": r["exp_pa"],
                                "components": r["components"], "tto_mult": r["tto_mult"]}
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

    out = {"date": date, "generated_at": dt.datetime.utcnow().isoformat() + "Z", "games": []}
    for g in slate:
        game_rec = {"game_id": g["game_id"], "away": g["away"], "home": g["home"],
                    "away_sp": g.get("away_sp"), "home_sp": g.get("home_sp"),
                    "sides": {}}
        for side in ("away", "home"):
            try:
                state = ingest_live.build_inputs_for_game(g, snap, side=side)
                game_rec["sides"][side] = predict_side(state)
            except Exception as e:
                game_rec["sides"][side] = {"error": str(e)}
        out["games"].append(game_rec)

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
