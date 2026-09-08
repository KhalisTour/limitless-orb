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


PITCHER_COEF_PREFIX = "p_"


def _apply_fitted_coefs_to(module, json_name: str):
    """Install a fitted logistic model into a models module, whole.

    Three things used to go wrong here and each of them moved every published
    number:

    1. Only five hard-coded feature names were copied across. A logistic fit's
       coefficients are valid *jointly* — the shipped HR fit carried xwoba at
       -0.198 largely to cancel xslg at +0.278 — so copying a subset silently
       doubled the net power term. Every feature the fit produced is installed
       now, and the module's feature list is replaced along with it, so a
       partial install is not expressible.
    2. The fit standardizes features against season-wide moments and saves them
       as feature_means / feature_stds. Nothing read them back, so the
       coefficients landed on z-scores taken over the nine hitters in that
       night's lineup — a much tighter spread, which inflated every z-score and
       put the coefficients on the wrong scale entirely. POP_STATS now carries
       the fit's own moments.
    3. CALIBRATION_DAMP was forced to 1.0 on the reasoning that a fitted model
       needs no damping. The fit is one of three ensemble members, so that does
       not follow; scored against real games it produced a top decile predicting
       37% against 21% actual. Calibration is left to calibrate.py.
    """
    p = MODELS_OUT / json_name
    if not p.exists():
        print(f"[predict] {json_name} not found — using {module.__name__} priors")
        return False
    fit = json.loads(p.read_text())
    coefs = fit.get("coefficients") or {}
    means = fit.get("feature_means") or {}
    stds = fit.get("feature_stds") or {}

    batter_keys = [k for k in coefs if not k.startswith(PITCHER_COEF_PREFIX)]
    pitcher_keys = [k for k in coefs if k.startswith(PITCHER_COEF_PREFIX)]

    new_m3 = {"intercept": float(fit["intercept"]),
              "matchup": module.M3.get("matchup", 0.0)}
    for k in batter_keys:
        new_m3[k] = float(coefs[k])
    module.M3 = new_m3
    module.M3_FEATURE_KEYS = tuple(batter_keys)
    module.M3_INTERCEPT_FITTED = True

    module.M3_PITCHER = {k: float(coefs[k]) for k in pitcher_keys}

    if means and stds:
        # MERGE, do not replace. M3 reads only the fitted features, but M1 and
        # M2 read others (ev, whiff, chase, xba depending on the module) and
        # those still need league moments — dropping them here would send just
        # those terms back to standardizing against the nine-hitter lineup,
        # which is the exact bug the fit's moments are here to close.
        fitted = {k: (float(means[k]), float(stds[k]) or 1.0)
                  for k in batter_keys if k in means and k in stds}
        module.POP_STATS = {**(module.POP_STATS or {}), **fitted}
        module.PITCHER_POP_STATS = {
            **(module.PITCHER_POP_STATS or {}),
            **{k: (float(means[k]), float(stds[k]) or 1.0)
               for k in pitcher_keys if k in means and k in stds}}
    else:
        print(f"[predict] {json_name} has no feature_means/feature_stds — "
              f"keeping default league moments; refit to pin the scale")

    print(f"[predict] applied fitted coefficients from {json_name}: "
          f"{len(batter_keys)} batter + {len(pitcher_keys)} pitcher terms")
    return True


def _apply_fitted_coefs():
    import models
    return _apply_fitted_coefs_to(models, "fitted_coefficients.json")


def _apply_calibration(module, target: str):
    """Install DAMP/SHIFT measured by calibrate.py, if they belong to this model.

    calibrate.py only writes a file when the fitted constants beat a constant
    league-rate forecast on a holdout, and stamps it with the model generation
    it was fitted against. A file from an older generation describes a model
    that is no longer running, so applying it would land a correction on top of
    a different model — refuse it and keep the shipped defaults.
    """
    p = MODELS_OUT / f"calibration_{target}.json"
    if not p.exists():
        print(f"[predict] no calibration_{target}.json — using default "
              f"damp={module.CALIBRATION_DAMP} shift={module.CALIBRATION_SHIFT}")
        return False
    cal = json.loads(p.read_text())
    gen = cal.get("model_generation")
    if gen != module.MODEL_GENERATION:
        print(f"[predict] calibration_{target}.json was fitted against model "
              f"generation {gen}, current is {module.MODEL_GENERATION} — ignoring "
              f"it and keeping the shipped defaults. Re-run score.py then "
              f"calibrate.py to refresh.")
        return False
    module.CALIBRATION_DAMP = float(cal["damp"])
    module.CALIBRATION_SHIFT = float(cal["shift"])
    print(f"[predict] applied {target} calibration: damp={cal['damp']:.3f} "
          f"shift={cal['shift']:+.3f} (fitted on {cal['n_rows']} scored rows)")
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


def _tensor_breakdown(tf, batter_id, arsenal):
    """Per-pitch-family detail for the matchup page: this batter's expected TB vs
    each family alongside how often this starter throws it. None when unavailable."""
    if tf is None or batter_id is None:
        return None
    etb = tf["etb"].get(str(batter_id))
    if etb is None:
        return None
    fams = tf["families"]
    raw = [float(arsenal.get(f, 0) or 0) for f in fams]
    s = sum(raw)
    if s <= 0:
        return None
    return [{"family": f, "etb": round(e, 4), "usage": round(u / s, 4)}
            for f, e, u in zip(fams, etb, raw)]


def _inject_state(state: dict):
    """Swap one game-side's inputs into all three model modules.

    The XBH and hit modules used to be updated separately and partially, so
    which model saw which pitcher depended on the order of the calls. All three
    read the same state here, and each is told to re-derive its league baseline
    afterwards — rebinding LEAGUE alone leaves M3's intercept pinned to the
    placeholder rate it was computed from at import.
    """
    import models, models_xbh, models_hit, sim
    for mod in (models, models_xbh, models_hit):
        mod.HITTERS = state["HITTERS"]
        mod.PITCHER = state["PITCHER"]
        mod.LEAGUE = state["LEAGUE"]
        mod.PITCH_FAMILIES = state["PITCH_FAMILIES"]
        mod.refresh_baseline()
    sim.HITTERS = state["HITTERS"]
    sim.LINEUP_ORDER = state["LINEUP_ORDER"]


def predict_side(state: dict, n_sims: int = 1000,
                  park_factor: float = 1.0, platoon_factors: dict = None,
                  pitcher_throws: str = None, tensor: dict = None) -> dict:
    _inject_state(state)
    import models, models_xbh, models_hit, sim
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
            # Park and platoon reach the XBH and hit ensembles now. They used to
            # be passed only to the HR model, so a Coors hitter got a
            # park-boosted HR number bolted onto a park-blind XBH number and a
            # total-bases figure that mixed the two conventions.
            p_hr = r["p_per_pa"]
            p_xbh = models_xbh.model5_ensemble(
                name, state["LINEUP_ORDER"],
                park_factor=park_factor, platoon_factor=plat_f)["p_per_pa"]
            p_hit = models_hit.model5_ensemble(
                name, state["LINEUP_ORDER"],
                park_factor=park_factor, platoon_factor=plat_f)["p_per_pa"]

            # Three independently calibrated models can disagree about their own
            # nesting: an XBH is a hit and a home run is an XBH, so the
            # probabilities have to be ordered. Clamp rather than rescale, so the
            # tighter-fit model wins and the looser one is pulled to meet it.
            p_xbh = max(p_xbh, p_hr)
            p_hit = max(p_hit, p_xbh)

            # E[TB]/PA = P(1B) + 2*P(2B) + 3*P(3B) + 4*P(HR), which in terms of
            # the nested probabilities is P(hit) + P(XBH) + P(3B) + 2*P(HR).
            # Triples are ~0.4% of PAs and not separately modelled, so they are
            # folded in at the league share of extra-base hits.
            triple_share = 0.048
            tb_level = p_hit + p_xbh + triple_share * (p_xbh - p_hr) + 2.0 * p_hr
            tb_d, xbh_d = _tensor_deltas(tensor, h.get("batter_id"), arsenal)
            exp_pa = r["exp_pa"]
            xbh_pa = min(models_xbh.P_MAX, max(p_hr, p_xbh + xbh_d))
            tb_pa = max(0.0, tb_level + tb_d)
            entry["xbh"] = {"per_pa_level": p_xbh, "tensor_delta": xbh_d,
                            "per_pa": xbh_pa, "exp_per_game": xbh_pa * exp_pa}
            entry["hit"] = {"per_pa": p_hit, "exp_per_game": p_hit * exp_pa}
            entry["tb"] = {"per_pa_level": tb_level, "tensor_delta": tb_d,
                           "per_pa": tb_pa, "exp_per_game": tb_pa * exp_pa,
                           "families": _tensor_breakdown(tensor, h.get("batter_id"), arsenal)}
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
    return {"per_hitter": per_hitter, "sim": sim_summary,
            "pitcher": state["PITCHER"]["name"],
            # Travels to the API so a side built on a half-posted lineup or a
            # league-average stand-in for the starter can be kept off the
            # top-picks board instead of ranking alongside real matchups.
            "data_quality": {
                "lineup": state.get("LINEUP_QUALITY") or {},
                "pitcher": state.get("PITCHER_QUALITY") or {},
            }}


def main(date: str = None):
    date = date or dt.date.today().isoformat()
    snap = ingest_live.SNAP_DIR / date
    if not snap.exists():
        print(f"[predict] no snapshot for {date}; running ingest first")
        snap = ingest_live.snapshot(date)
    slate = json.loads((snap / "slate.json").read_text())

    import models, models_xbh, models_hit
    _apply_fitted_coefs()
    _apply_fitted_coefs_to(models_xbh, "fitted_coefficients_xbh.json")
    _apply_fitted_coefs_to(models_hit, "fitted_coefficients_hit.json")
    for mod, tgt in ((models, "hr"), (models_xbh, "xbh"), (models_hit, "hit")):
        _apply_calibration(mod, tgt)
    tensor = _load_tensor()

    park_factors = _load_park_factors()
    platoon_factors_map = _load_platoon_factors()

    import models as _m
    out = {"date": date, "generated_at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
           # Stamped so score.py can tell which model generation produced a row
           # and calibrate.py will not fit constants across a model change.
           "model_generation": _m.MODEL_GENERATION,
           "games": []}
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
