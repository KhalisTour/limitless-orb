"""
models_hit.py — The five hit-likelihood models (all hits: 1B + 2B + 3B + HR).

Unlike models_xbh.py, this one is NOT a rescaled HR model. It used to be — the
file was a copy of models.py with the baseline changed — and that is a bigger
error here than it was for XBH, because a hit is not a power outcome.

Measured over data/backtest.csv (110,256 PAs joined to the season batter board),
regressing the HR model's own composite score against the hit label gives a
slope of -0.007 log-odds per SD. Not weak: zero. The HR-shaped model carried no
information about whether a plate appearance ends in a hit, and the pipeline was
publishing its output as a hit probability anyway.

The features that do move the hit rate point the other way:

    feature   HR      HIT     (univariate log-odds per +1 league SD)
    barrel   +0.294  -0.008
    la       +0.138  -0.059
    whiff    +0.202  -0.062
    k        +0.149  -0.072
    xba      +0.055  +0.100

Swing-and-miss is the strongest example. It is a power marker for HR and a
straightforward tax on batting average, so a single weight cannot serve both.
Contact quality (xba) leads here, and lift is a mild negative — a ball in the
air is more likely to be caught than a ball on the ground.
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
        if POP_STATS and "xba" in POP_STATS:
            prior = POP_STATS["xba"][0]
        else:
            vals = _pop("xba")
            prior = mean(vals) if vals else 0.25
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
# HIT BASELINE
# --------------------------------------------------------------------------
# 0.2178 measured over backtest.csv; the old 0.245 was a guess ~12% high.
HIT_PER_PA = LEAGUE.get("hit_per_pa", 0.218)


# --------------------------------------------------------------------------
# MODEL 1 — Linear Composite Score
# --------------------------------------------------------------------------
# Univariate log-odds per +1 league SD against the hit label, normalized so the
# absolute weights sum to 1. Signs are the measured signs, which is why this
# vector barely resembles the HR one: expected batting average leads, strikeout
# and whiff rate are real negatives, and barrel% is close to inert.
M1_WEIGHTS = dict(xba=0.27, k=-0.20, whiff=-0.17, la=-0.16,
                  xslg=0.13, hardhit=0.06, barrel=-0.02)

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

    xba_z    = zscore("xba",   h.get("xba"))
    whiff_z  = zscore("whiff", h.get("whiff"))
    k_z      = zscore("k",     h.get("k"))
    chase_z  = zscore("chase", h.get("chase"))
    la_z     = zscore("la",    h.get("la"))

    # Damage proxies for hits are contact proxies. Against every family the
    # question is "does this hitter put the ball in play well", so bat-to-ball
    # skill drives all three and the families differ in what beats it: rising
    # fastballs are beaten by pure contact, sinkers punish the hitter who
    # already hits the ball into the ground, and offspeed punishes chasers.
    dmg_rise = xba_z - 0.6 * whiff_z - 0.2 * la_z
    dmg_sink = xba_z - 0.4 * k_z + 0.2 * la_z
    dmg_soft = xba_z - 0.5 * chase_z - 0.5 * whiff_z

    # Hits are suppressed by a pitcher who misses bats, not by one who avoids
    # hard contact — so this model reads the starter's whiff rate where the HR
    # model reads barrel% allowed. Signed so an above-average whiff rate makes
    # the matchup worse for the hitter.
    p_whiff = PITCHER.get("whiff") or LEAGUE["whiff"]
    vuln_mult = 1.0 - (p_whiff - LEAGUE["whiff"]) / LEAGUE["whiff"]

    return (usage["rise"] * dmg_rise +
            usage["sink"] * dmg_sink +
            usage["soft"] * dmg_soft) * vuln_mult


# --------------------------------------------------------------------------
# MODEL 3 — Logistic Per-PA Hit Probability
# --------------------------------------------------------------------------

# Regularized logistic regression on data/backtest.csv, selected on a
# time-based holdout (C=0.01, holdout AUC 0.519). Modest, but honestly modest:
# per-PA hit outcomes are close to a coin flip against season rate stats, and
# the previous file reported an in-sample 0.555 for a model whose out-of-sample
# signal against this label was nil.
M3 = dict(
    intercept=math.log(HIT_PER_PA / (1 - HIT_PER_PA)),
    xba=0.070, k=-0.035, la=-0.034, hardhit=0.010,
    matchup=0.04,   # unfit prior, scaled to the hit model's measured slope
)

M3_FEATURE_KEYS = ("xba", "k", "la", "hardhit")

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
    # xba is this model's anchor feature, so it is the one that gets shrunk.
    xba_z = zscore("xba", shrink(h.get("xba"), h.get("bbe")))
    z = M3["intercept"] + M3.get("matchup", 0.0) * model2_matchup(name)
    for f in M3_FEATURE_KEYS:
        v = xba_z if f == "xba" else zscore(f, h.get(f))
        z += M3.get(f, 0.0) * v
    z += sum(c * _pitcher_z(f) for f, c in M3_PITCHER.items())
    return logistic(z)


# --------------------------------------------------------------------------
# LEAGUE BASELINE REFRESH
# --------------------------------------------------------------------------

M3_INTERCEPT_FITTED = False


def refresh_baseline():
    """Re-derive baseline-dependent constants after LEAGUE has been swapped in.

    HIT_PER_PA is read at import time from data.py's placeholder, so rebinding
    LEAGUE alone left the model anchored to a stale league rate. backtest.py
    writes the measured hit rate into data/base_rates.json and ingest_live
    passes it through; this picks it up. predict_today calls it.
    """
    global HIT_PER_PA
    v = LEAGUE.get("hit_per_pa")
    if v:
        HIT_PER_PA = float(v)
    if not M3_INTERCEPT_FITTED:
        M3["intercept"] = logit(HIT_PER_PA)


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
M1_SLOPE = 0.165   # per unit of the M1 composite (hit label)
M2_SLOPE = 0.067   # per unit of the M2 matchup score

# Fitted on the train window and checked on a held-out later window
# (see calibrate.py). Holdout log-loss is flat between 0.60 and 0.75 for all
# three targets and rises either side, so 0.70 is the shallow optimum rather
# than a tuned-to-the-decimal number. At this setting every target beats a
# constant league-rate prediction on both Brier and log-loss; the shipped model
# used to lose to that baseline on HR.
CALIBRATION_DAMP = 0.70    # slope on the log-odds deviation from baseline
CALIBRATION_SHIFT = 0.0    # constant log-odds offset applied after the slope

# Guard, not a working constraint: a .350 hitter is ~0.31 hits per PA.
P_MAX = 0.60

def _calibrate(p):
    lo_b = logit(HIT_PER_PA)
    return logistic(lo_b + CALIBRATION_SHIFT + CALIBRATION_DAMP * (logit(p) - lo_b))

def _score_to_prob(score, slope):
    """Map a raw model score to a probability anchored at the league baseline."""
    return logistic(logit(HIT_PER_PA) + slope * score)

def model5_ensemble(name, lineup_order, park_factor=1.0, platoon_factor=1.0):
    p_lin = _score_to_prob(model1_linear(name), M1_SLOPE)
    p_mat = _score_to_prob(model2_matchup(name), M2_SLOPE)
    p_log = model3_logistic(name)

    p = (ENSEMBLE_W["logistic"] * p_log +
         ENSEMBLE_W["matchup"]  * p_mat +
         ENSEMBLE_W["linear"]   * p_lin)

    ctx = model4_context(name, lineup_order, park_factor=park_factor,
                         platoon_factor=platoon_factor)
    # Park factors are HR-scaled: a park that plays 20% hot for home runs does
    # not give up 20% more singles. Damp the park component toward 1 for hits
    # and leave platoon and times-through-order at full strength.
    park_hit = 1.0 + 0.35 * (ctx["park_factor"] - 1.0)
    mult = ctx["tto_mult"] / max(1e-6, ctx["park_factor"]) * park_hit
    p_adj = logistic(logit(_calibrate(p)) + math.log(max(1e-6, mult)))
    p_adj = min(P_MAX, p_adj)
    return dict(p_per_pa=p_adj, exp_pa=ctx["exp_pa"],
                components=dict(logistic=p_log, matchup=p_mat, linear=p_lin),
                tto_mult=mult,
                park_factor=ctx["park_factor"],
                platoon_factor=ctx["platoon_factor"])
