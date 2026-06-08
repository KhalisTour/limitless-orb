"""
models.py — The five HR-likelihood models.

Each model is a pure function of the data dicts in data.py. Read top-to-bottom:
the helpers (z-scoring, shrinkage) come first, then Models 1-5 in order.

Design philosophy: every model returns a per-PLATE-APPEARANCE HR probability
(or a score that gets mapped to one), so they're directly comparable and can be
fed into one shared Monte Carlo game simulator (sim.py).
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
# MODEL 1 — Linear Composite "Barrel Expectancy" Score
#   Weighted sum of z-scored HR-antecedent stats. Cheap, interpretable baseline.
#   Output: a unitless score, later mapped to a probability via calibration.
# --------------------------------------------------------------------------

M1_WEIGHTS = dict(barrel=0.28, xslg=0.20, hardhit=0.16, ev=0.12, k=-0.10, la=0.08, whiff=-0.06)

def model1_linear(name):
    h = HITTERS[name]
    score = sum(w * zscore(f, h.get(f)) for f, w in M1_WEIGHTS.items())
    return score


# --------------------------------------------------------------------------
# MODEL 2 — Matchup Interaction (pitch-family x hitter geometry)
#   Multiplicative: rewards the OVERLAP of hitter strength and pitcher usage.
#   For each pitch family, hitter "damage" is scaled by how much the pitcher
#   throws it and how much contact-quality the pitcher allows.
# --------------------------------------------------------------------------

def _family_usage():
    a = PITCHER["arsenal"]
    return {fam: sum(a[p] for p in pitches) / 100.0
            for fam, pitches in PITCH_FAMILIES.items()}

def model2_matchup(name):
    h = HITTERS[name]
    usage = _family_usage()

    # Hitter damage proxies per family:
    #  - vs 'rise' (elevated hard stuff): rewards barrel + low whiff + lift (LA)
    #  - vs 'sink' (down in zone): rewards barrel but penalizes ground tendency (topped)
    #  - vs 'soft' (offspeed/breaking): penalizes chase + whiff (gets expanded on)
    barrel_z = zscore("barrel", h.get("barrel"))
    whiff_z  = zscore("whiff",  h.get("whiff"))    # may be 0 if missing
    la_z     = zscore("la",     h.get("la"))
    topped_z = zscore("topped", h.get("topped"))
    chase_z  = zscore("chase",  h.get("chase"))

    dmg_rise = barrel_z + 0.5 * la_z - 0.5 * whiff_z
    dmg_sink = barrel_z - 0.4 * topped_z
    dmg_soft = -0.6 * chase_z - 0.6 * whiff_z + 0.3 * barrel_z

    pitcher_vuln = ((PITCHER.get("barrel") or LEAGUE["barrel"]) - LEAGUE["barrel"]) / LEAGUE["barrel"]
    vuln_mult = 1.0 + pitcher_vuln  # ~1.41 here (10.6 vs 7.5 baseline)

    score = (usage["rise"] * dmg_rise +
             usage["sink"] * dmg_sink +
             usage["soft"] * dmg_soft) * vuln_mult
    return score


# --------------------------------------------------------------------------
# MODEL 3 — Logistic Per-PA HR Probability (the calibrated workhorse)
#   Binary outcome -> logistic link. Intercept set so an average hitter vs an
#   average pitcher lands at the league HR/PA baseline. Everything else shifts
#   the log-odds up or down. Uses SHRUNK barrel% so small samples don't explode.
# --------------------------------------------------------------------------

# Coefficients are on z-scored inputs (except intercept). Reasoned, not fit —
# see README "Data integrity" for why these are priors awaiting calibration.
M3 = dict(
    intercept=math.log(LEAGUE["hr_per_pa"] / (1 - LEAGUE["hr_per_pa"])),  # baseline log-odds
    barrel=0.55, xslg=0.35, hardhit=0.20, la=0.15, whiff=-0.20, matchup=0.40,
)

def model3_logistic(name):
    h = HITTERS[name]
    barrel_shrunk = shrink(h["barrel"], h["bbe"])
    # z-score the shrunk barrel against the (also-shrunk) population for consistency
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
    return logistic(z)   # per-PA HR probability


# --------------------------------------------------------------------------
# MODEL 4 — Context / Leverage Multiplier
#   Not a standalone predictor — a multiplier on Model 3. Captures times-through-
#   the-order decay (starter gets hit harder 3rd time) and lineup-slot PA volume.
# --------------------------------------------------------------------------

def model4_context(name, lineup_order):
    slot = lineup_order.index(name)  # 0-indexed
    # Top of order sees the starter more times and gets the 3rd-TTO penalty pitch.
    tto_bonus = 1.0 + max(0, (4 - slot)) * 0.04   # slots 1-4 get up to +12%
    # Expected PAs by slot scales opportunity (used by sim, returned for transparency)
    exp_pa = 4.6 - slot * 0.12
    return dict(tto_mult=tto_bonus, exp_pa=exp_pa)


# --------------------------------------------------------------------------
# MODEL 5 — Ensemble with shrinkage
#   Blends M1 (linear), M2 (matchup), M3 (logistic) into one per-PA probability.
#   M1/M2 are scores, not probabilities, so they're squashed through a logistic
#   centered on the population before blending. M3 is already a probability.
# --------------------------------------------------------------------------

ENSEMBLE_W = dict(logistic=0.45, matchup=0.30, linear=0.25)

# CALIBRATION: the z-scored coefficients in M3 are reasoned priors, not fit to
# labeled HR outcomes, so raw output runs hot. Until real data is available we
# damp the deviation from baseline by this factor. With a labeled dataset this
# whole block is REPLACED by fitting M3's coefficients via maximum likelihood.
CALIBRATION_DAMP = 0.45   # 0=everyone at baseline, 1=raw (uncalibrated) output

def _calibrate(p):
    base = LEAGUE["hr_per_pa"]
    lo_p, lo_b = math.log(p/(1-p)), math.log(base/(1-base))
    return logistic(lo_b + CALIBRATION_DAMP * (lo_p - lo_b))

def _score_to_prob(scores, this_score):
    """Map a population of raw scores to probabilities anchored at league baseline."""
    mu, sd = mean(scores), (pstdev(scores) or 1.0)
    base_logodds = math.log(LEAGUE["hr_per_pa"] / (1 - LEAGUE["hr_per_pa"]))
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
    p_adj = min(0.5, p_cal * ctx["tto_mult"])   # cap to keep probabilities sane
    return dict(p_per_pa=p_adj, exp_pa=ctx["exp_pa"],
                components=dict(logistic=p_log, matchup=p_mat, linear=p_lin),
                tto_mult=ctx["tto_mult"])
