"""
models_xbh.py — The five XBH-likelihood models (extra-base hits: 2B + 3B + HR).

Same five-model structure as models.py, but every weight, spread and baseline
is estimated for XBH rather than inherited from the HR model.

That inheritance was the bug this file used to have: apart from the baseline
constant it was a byte-for-byte copy of models.py, so "XBH" was the HR model
with a different label on the y-axis. Across a slate the two ranked hitters
almost identically (Spearman 0.96), and the M1/M2 scores were spread as if
predicting HR — which moves ~2.2x more log-odds per SD than XBH does — so the
output ran hot on top of it.

Measured over data/backtest.csv (110,256 PAs joined to the season batter board):
XBH keeps the *direction* of the HR model — it is mostly a power outcome, and
the same contact-quality features point the same way — at roughly 45% of the
per-SD slope. So the shape below is HR-like on purpose; the magnitudes are not.
"""

import math
from statistics import mean, pstdev
from data import HITTERS, PITCHER, PITCH_FAMILIES, LEAGUE, LEAGUE_MOMENTS


# Bumped whenever a change to this module makes previously-scored predictions
# non-comparable — new features, a new score scale, a new baseline anchor.
# score.py stamps each logged row with it, calibrate.py only fits on rows from
# the running generation, and predict_today refuses a calibration file stamped
# with a different one. Without that, the constants fitted to one model get
# applied on top of another and the correction lands twice.
#   1: original ensemble (lineup-relative z-scores, 0.6 score spread)
#   2: league-anchored z-scores, measured per-target slopes, mean-neutral TTO
MODEL_GENERATION = 2


# --------------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------------

POP_STATS = dict(LEAGUE_MOMENTS)   # {field: (mean, sd)}; see models.py


def _pop(field):
    """Population values for a field across all hitters (ignoring None)."""
    return [h[field] for h in HITTERS.values() if h.get(field) is not None]

def zscore(field, value):
    """Standardize a value against the reference population. None -> 0 (neutral)."""
    if value is None:
        return 0.0
    if POP_STATS and field in POP_STATS:
        mu, sd = POP_STATS[field]
        return (value - mu) / (sd or 1.0)
    vals = _pop(field)
    if not vals:
        return 0.0
    mu, sd = mean(vals), (pstdev(vals) or 1.0)
    return (value - mu) / sd

def shrink(raw_rate, bbe, k=150, prior=None):
    """Regress a rate toward the population mean by batted-ball sample size."""
    if prior is None:
        if POP_STATS and "barrel" in POP_STATS:
            prior = POP_STATS["barrel"][0]
        else:
            vals = _pop("barrel")
            prior = mean(vals) if vals else 7.5
    if raw_rate is None:
        return prior
    if bbe is None or bbe == 0:
        return raw_rate
    w = bbe / (bbe + k)
    return w * raw_rate + (1 - w) * prior

def logistic(z):
    return 1.0 / (1.0 + math.exp(-z))

def logit(p, eps=1e-9):
    p = min(1.0 - eps, max(eps, p))
    return math.log(p / (1.0 - p))


# --------------------------------------------------------------------------
# XBH BASELINE
# --------------------------------------------------------------------------
# 0.0759 measured over backtest.csv. The old 0.09 was a guess and put every
# hitter on the slate ~18% above the real league rate before any feature was
# read. ingest_live overrides this from data/base_rates.json when present.
XBH_PER_PA = LEAGUE.get("xbh_per_pa", 0.076)


# --------------------------------------------------------------------------
# MODEL 1 — Linear Composite Score
# --------------------------------------------------------------------------
# Univariate log-odds per +1 league SD against the XBH label, normalized to sum
# to 1. Note this is the same ordering as the HR weights, which is the honest
# answer: the features that make a home run likely are the features that make a
# double likely. The difference between the two models is the SLOPE, carried by
# M1_SLOPE below, not the mixing vector.
M1_WEIGHTS = dict(hardhit=0.20, ev=0.19, xslg=0.19, barrel=0.19,
                  whiff=0.09, la=0.07, k=0.07)

def model1_linear(name):
    h = HITTERS[name]
    return sum(w * zscore(f, h.get(f)) for f, w in M1_WEIGHTS.items())


# --------------------------------------------------------------------------
# MODEL 2 — Matchup Interaction (pitch-family × hitter geometry)
# --------------------------------------------------------------------------

def _family_usage():
    """Share of pitches thrown in each family, normalized to sum to 1."""
    a = PITCHER.get("arsenal") or {}
    fam_raw = {fam: sum(float(a.get(p, 0) or 0) for p in pitches)
               for fam, pitches in PITCH_FAMILIES.items()}
    total = sum(fam_raw.values())
    if total <= 0:
        return {fam: 0.0 for fam in PITCH_FAMILIES}
    return {fam: v / total for fam, v in fam_raw.items()}

def model2_matchup(name):
    h = HITTERS[name]
    usage = _family_usage()

    barrel_z = zscore("barrel", h.get("barrel"))
    whiff_z  = zscore("whiff",  h.get("whiff"))
    la_z     = zscore("la",     h.get("la"))
    hardhit_z = zscore("hardhit", h.get("hardhit"))
    chase_z  = zscore("chase",  h.get("chase"))

    # Damage proxies leaned toward XBH rather than HR. A double needs the ball
    # struck hard into a gap, not lifted over a wall, so hard-hit rate carries
    # more of the load than launch angle and extreme lift is not rewarded the
    # way it is for HR. The 'topped' term the HR model uses is dropped: the
    # Savant board this pipeline pulls returns topped_percent empty for every
    # hitter, so it only ever contributed the fill-in default.
    dmg_rise = 0.6 * barrel_z + 0.5 * hardhit_z + 0.2 * la_z - 0.4 * whiff_z
    dmg_sink = 0.5 * barrel_z + 0.6 * hardhit_z
    dmg_soft = 0.3 * barrel_z + 0.3 * hardhit_z - 0.5 * chase_z - 0.5 * whiff_z

    pitcher_vuln = ((PITCHER.get("barrel") or LEAGUE["barrel"]) - LEAGUE["barrel"]) / LEAGUE["barrel"]
    vuln_mult = 1.0 + pitcher_vuln

    return (usage["rise"] * dmg_rise +
            usage["sink"] * dmg_sink +
            usage["soft"] * dmg_soft) * vuln_mult


# --------------------------------------------------------------------------
# MODEL 3 — Logistic Per-PA XBH Probability
# --------------------------------------------------------------------------

# Regularized logistic regression on data/backtest.csv, selected on a
# time-based holdout (C=1.0, holdout AUC 0.538). Adding whiff/k/xwoba on top of
# these four scored *worse* out of sample and flipped signs, so they are out.
M3 = dict(
    intercept=math.log(XBH_PER_PA / (1 - XBH_PER_PA)),
    barrel=0.014, xslg=0.055, hardhit=0.097, la=0.046,
    matchup=0.09,   # unfit prior, scaled to XBH's measured per-SD slope
)

M3_FEATURE_KEYS = ("barrel", "xslg", "hardhit", "la")

M3_PITCHER = dict(p_barrel=0.0, p_hardhit=0.0, p_xslg=0.0,
                  p_xwoba=0.0, p_k=0.0, p_whiff=0.0)
PITCHER_POP_STATS = None


def _pitcher_z(field):
    if not PITCHER_POP_STATS or field not in PITCHER_POP_STATS:
        return 0.0
    v = PITCHER.get(field[2:])
    if v is None:
        return 0.0
    mu, sd = PITCHER_POP_STATS[field]
    return (v - mu) / (sd or 1.0)


def model3_logistic(name):
    h = HITTERS[name]
    barrel_z = zscore("barrel", shrink(h.get("barrel"), h.get("bbe")))
    z = M3["intercept"] + M3.get("matchup", 0.0) * model2_matchup(name)
    for f in M3_FEATURE_KEYS:
        v = barrel_z if f == "barrel" else zscore(f, h.get(f))
        z += M3.get(f, 0.0) * v
    z += sum(c * _pitcher_z(f) for f, c in M3_PITCHER.items())
    return logistic(z)


# --------------------------------------------------------------------------
# LEAGUE BASELINE REFRESH
# --------------------------------------------------------------------------

M3_INTERCEPT_FITTED = False


def refresh_baseline():
    """Re-derive baseline-dependent constants after LEAGUE has been swapped in.

    XBH_PER_PA is read at import time from data.py's placeholder, so rebinding
    LEAGUE alone left the model anchored to a stale league rate. backtest.py
    writes the measured XBH rate into data/base_rates.json and ingest_live
    passes it through; this picks it up. predict_today calls it.
    """
    global XBH_PER_PA
    v = LEAGUE.get("xbh_per_pa")
    if v:
        XBH_PER_PA = float(v)
    if not M3_INTERCEPT_FITTED:
        M3["intercept"] = logit(XBH_PER_PA)


# --------------------------------------------------------------------------
# MODEL 4 — Context / Leverage Multiplier
# --------------------------------------------------------------------------

# Mean of the raw TTO ladder over a nine-man lineup.
TTO_MEAN = sum(1.0 + max(0, (4 - s)) * 0.04 for s in range(9)) / 9.0


def model4_context(name, lineup_order, park_factor=1.0, platoon_factor=1.0):
    try:
        slot = lineup_order.index(name)
    except ValueError:
        slot = 4
    # Times-through-order lift, normalized to average 1.0 across a nine-man
    # lineup. The raw ladder (1.16 at leadoff down to 1.00 from slot 5) only
    # ever multiplies UP, so applying it to all nine slots added 4.4% to every
    # slate's expected output — a level bias that calibration then had to
    # absorb as if it were spread. Dividing by the ladder's own mean keeps the
    # slot-to-slot ordering and makes the term redistribute instead of inflate.
    tto_bonus = (1.0 + max(0, (4 - slot)) * 0.04) / TTO_MEAN
    combined_mult = tto_bonus * park_factor * platoon_factor
    exp_pa = 4.6 - slot * 0.12
    return dict(tto_mult=combined_mult, exp_pa=exp_pa,
                park_factor=park_factor, platoon_factor=platoon_factor)


# --------------------------------------------------------------------------
# MODEL 5 — Ensemble with shrinkage
# --------------------------------------------------------------------------

ENSEMBLE_W = dict(logistic=0.45, matchup=0.30, linear=0.25)

# Log-odds the outcome actually moves per unit of each score, regressed on
# 110,256 plate appearances in data/backtest.csv. These replace a single
# hard-coded 0.6 "spread" that was shared by all three targets and applied to a
# score z-scored against the CURRENT LINEUP — which made a hitter's published
# probability depend on who else was batting that night, and re-centred every
# lineup so that the best bat on a bad team scored like the best bat in
# baseball. Anchoring on measured per-unit slopes removes the lineup from the
# calculation entirely.
M1_SLOPE = 0.201   # per unit of the M1 composite (XBH label)
M2_SLOPE = 0.141   # per unit of the M2 matchup score

# Fitted on the train window and checked on a held-out later window
# (see calibrate.py). Holdout log-loss is flat between 0.60 and 0.75 for all
# three targets and rises either side, so 0.70 is the shallow optimum rather
# than a tuned-to-the-decimal number. At this setting every target beats a
# constant league-rate prediction on both Brier and log-loss; the shipped model
# used to lose to that baseline on HR.
CALIBRATION_DAMP = 0.70    # slope on the log-odds deviation from baseline
CALIBRATION_SHIFT = 0.0    # constant log-odds offset applied after the slope

# Guard, not a working constraint: the best XBH/PA seasons sit near 0.16.
P_MAX = 0.30

def _calibrate(p):
    lo_b = logit(XBH_PER_PA)
    return logistic(lo_b + CALIBRATION_SHIFT + CALIBRATION_DAMP * (logit(p) - lo_b))

def _score_to_prob(score, slope):
    """Map a raw model score to a probability anchored at the league baseline."""
    return logistic(logit(XBH_PER_PA) + slope * score)

def model5_ensemble(name, lineup_order, park_factor=1.0, platoon_factor=1.0):
    p_lin = _score_to_prob(model1_linear(name), M1_SLOPE)
    p_mat = _score_to_prob(model2_matchup(name), M2_SLOPE)
    p_log = model3_logistic(name)

    p = (ENSEMBLE_W["logistic"] * p_log +
         ENSEMBLE_W["matchup"]  * p_mat +
         ENSEMBLE_W["linear"]   * p_lin)

    ctx = model4_context(name, lineup_order, park_factor=park_factor,
                         platoon_factor=platoon_factor)
    # Park and platoon reach this model at all now. They used to stop at the HR
    # ensemble, so a hitter in Coors got a park-boosted HR number and a
    # park-blind XBH number, and the total-bases estimate mixed the two.
    p_adj = logistic(logit(_calibrate(p)) + math.log(max(1e-6, ctx["tto_mult"])))
    p_adj = min(P_MAX, p_adj)
    return dict(p_per_pa=p_adj, exp_pa=ctx["exp_pa"],
                components=dict(logistic=p_log, matchup=p_mat, linear=p_lin),
                tto_mult=ctx["tto_mult"],
                park_factor=ctx["park_factor"],
                platoon_factor=ctx["platoon_factor"])
