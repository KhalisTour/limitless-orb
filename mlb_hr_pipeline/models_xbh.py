"""
models_xbh.py — The five XBH-likelihood models (extra-base hits: 2B + 3B + HR).

Stub: mirrors models.py structure but with baseline calibrated to XBH/PA ≈ 0.09.
All weights are TODO placeholders carried over from models.py pending proper fitting.
"""

import math
from statistics import mean, pstdev
from data import HITTERS, PITCHER, PITCH_FAMILIES, LEAGUE


# --------------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------------

def _pop(field):
    """Population values for a field across all hitters (ignoring None)."""
    return [h[field] for h in HITTERS.values() if h.get(field) is not None]

def zscore(field, value):
    """Standardize a value against the hitter population. None -> 0 (neutral)."""
    if value is None:
        return 0.0
    vals = _pop(field)
    mu, sd = mean(vals), (pstdev(vals) or 1.0)
    return (value - mu) / sd

def shrink(raw_rate, bbe, k=150, prior=None):
    if prior is None:
        prior = mean([r for r in _pop("barrel")]) if _pop("barrel") else 7.5
    if raw_rate is None:
        return prior
    if bbe is None or bbe == 0:
        return prior
    w = bbe / (bbe + k)
    return w * raw_rate + (1 - w) * prior

def logistic(z):
    return 1.0 / (1.0 + math.exp(-z))


# --------------------------------------------------------------------------
# XBH BASELINE
# --------------------------------------------------------------------------
XBH_PER_PA = 0.09  # league XBH per PA baseline


# --------------------------------------------------------------------------
# MODEL 1 — Linear Composite Score (TODO: weights are HR placeholders)
# --------------------------------------------------------------------------

M1_WEIGHTS = dict(barrel=0.28, xslg=0.20, hardhit=0.16, ev=0.12, k=-0.10, la=0.08, whiff=-0.06)  # TODO: fit for XBH

def model1_linear(name):
    h = HITTERS[name]
    score = sum(w * zscore(f, h.get(f)) for f, w in M1_WEIGHTS.items())
    return score


# --------------------------------------------------------------------------
# MODEL 2 — Matchup Interaction (TODO: weights are HR placeholders)
# --------------------------------------------------------------------------

def _family_usage():
    a = PITCHER["arsenal"]
    return {fam: sum(a[p] for p in pitches) / 100.0
            for fam, pitches in PITCH_FAMILIES.items()}

def model2_matchup(name):
    h = HITTERS[name]
    usage = _family_usage()

    barrel_z = zscore("barrel", h.get("barrel"))
    whiff_z  = zscore("whiff",  h.get("whiff"))
    la_z     = zscore("la",     h.get("la"))
    topped_z = zscore("topped", h.get("topped"))
    chase_z  = zscore("chase",  h.get("chase"))

    dmg_rise = barrel_z + 0.5 * la_z - 0.5 * whiff_z      # TODO: fit for XBH
    dmg_sink = barrel_z - 0.4 * topped_z                    # TODO: fit for XBH
    dmg_soft = -0.6 * chase_z - 0.6 * whiff_z + 0.3 * barrel_z  # TODO: fit for XBH

    pitcher_vuln = ((PITCHER.get("barrel") or LEAGUE["barrel"]) - LEAGUE["barrel"]) / LEAGUE["barrel"]
    vuln_mult = 1.0 + pitcher_vuln

    score = (usage["rise"] * dmg_rise +
             usage["sink"] * dmg_sink +
             usage["soft"] * dmg_soft) * vuln_mult
    return score


# --------------------------------------------------------------------------
# MODEL 3 — Logistic Per-PA XBH Probability
# --------------------------------------------------------------------------

M3 = dict(
    intercept=math.log(XBH_PER_PA / (1 - XBH_PER_PA)),  # baseline log-odds for XBH
    barrel=0.55, xslg=0.35, hardhit=0.20, la=0.15, whiff=-0.20, matchup=0.40,  # TODO: fit for XBH
)

def model3_logistic(name):
    h = HITTERS[name]
    barrel_shrunk = shrink(h["barrel"], h["bbe"])
    shrunk_pop = [shrink(HITTERS[n]["barrel"], HITTERS[n]["bbe"]) for n in HITTERS]
    mu, sd = mean(shrunk_pop), (pstdev(shrunk_pop) or 1.0)
    barrel_z = (barrel_shrunk - mu) / sd

    z = (M3["intercept"]
         + M3["barrel"]  * barrel_z
         + M3["xslg"]    * zscore("xslg", h.get("xslg"))
         + M3["hardhit"] * zscore("hardhit", h.get("hardhit"))
         + M3["la"]      * zscore("la", h.get("la"))
         + M3["whiff"]   * zscore("whiff", h.get("whiff"))
         + M3["matchup"] * model2_matchup(name))
    return logistic(z)


# --------------------------------------------------------------------------
# MODEL 4 — Context / Leverage Multiplier
# --------------------------------------------------------------------------

def model4_context(name, lineup_order):
    slot = lineup_order.index(name)
    tto_bonus = 1.0 + max(0, (4 - slot)) * 0.04
    exp_pa = 4.6 - slot * 0.12
    return dict(tto_mult=tto_bonus, exp_pa=exp_pa)


# --------------------------------------------------------------------------
# MODEL 5 — Ensemble with shrinkage
# --------------------------------------------------------------------------

ENSEMBLE_W = dict(logistic=0.45, matchup=0.30, linear=0.25)  # TODO: fit for XBH

CALIBRATION_DAMP = 0.45  # TODO: calibrate for XBH

def _calibrate(p):
    base = XBH_PER_PA
    lo_p, lo_b = math.log(p/(1-p)), math.log(base/(1-base))
    return logistic(lo_b + CALIBRATION_DAMP * (lo_p - lo_b))

def _score_to_prob(scores, this_score):
    """Map a population of raw scores to probabilities anchored at XBH baseline."""
    mu, sd = mean(scores), (pstdev(scores) or 1.0)
    base_logodds = math.log(XBH_PER_PA / (1 - XBH_PER_PA))
    return logistic(base_logodds + 0.6 * (this_score - mu) / sd)

def model5_ensemble(name, lineup_order):
    m1_all = {n: model1_linear(n) for n in HITTERS}
    m2_all = {n: model2_matchup(n) for n in HITTERS}

    p_lin = _score_to_prob(list(m1_all.values()), m1_all[name])
    p_mat = _score_to_prob(list(m2_all.values()), m2_all[name])
    p_log = model3_logistic(name)

    p = (ENSEMBLE_W["logistic"] * p_log +
         ENSEMBLE_W["matchup"]  * p_mat +
         ENSEMBLE_W["linear"]   * p_lin)

    ctx = model4_context(name, lineup_order)
    p_cal = _calibrate(p)
    p_adj = min(0.5, p_cal * ctx["tto_mult"])
    return dict(p_per_pa=p_adj, exp_pa=ctx["exp_pa"],
                components=dict(logistic=p_log, matchup=p_mat, linear=p_lin),
                tto_mult=ctx["tto_mult"])
