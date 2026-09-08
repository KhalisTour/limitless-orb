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

# Population moments the features are standardized against. When None, a
# feature is z-scored against the hitters currently in HITTERS — which, once
# predict_today swaps in a live lineup, is a nine-man population. That makes
# the best bat in a weak lineup a +2.5 sigma hitter and it makes the fitted
# coefficients (fit on season-wide moments) wrong by the ratio of the two
# spreads. Defaults to the league moments the shipped coefficients were fit on;
# predict_today overrides it with the fit's own feature_means/feature_stds so
# coefficients are always applied on the scale they were estimated on. Set to
# None to fall back to standardizing against whoever is in HITTERS.
POP_STATS = dict(LEAGUE_MOMENTS)   # {field: (mean, sd)}


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
    """Regress a rate toward the population mean by batted-ball sample size.

    `bbe` is unknown on the Savant board this pipeline pulls, so ingest fills a
    constant default. Shrinking every hitter by that same constant would just
    scale barrel% deviations uniformly (at the default it halves them), which
    silently shrinks the model's strongest feature without adding information —
    so a caller that passes no real count gets the raw rate back instead.
    """
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
# MODEL 1 — Linear Composite "Barrel Expectancy" Score
#   Weighted sum of z-scored HR-antecedent stats. Cheap, interpretable baseline.
#   Output: a unitless score, later mapped to a probability via calibration.
# --------------------------------------------------------------------------

# Per the design doc, M1's weights encode each feature's MARGINAL association
# with the outcome — the one-feature-at-a-time direction, deliberately not the
# joint fit (that is M3's job). They are the univariate log-odds per +1 league
# SD measured over data/backtest.csv, normalized to sum to 1.
#
# Two of the old weights had the wrong sign. K% and whiff% were entered
# negatively as "opportunity suppressors", but measured against real PAs both
# run POSITIVE for home runs (+0.15 and +0.20 log-odds per SD): at the level of
# season rates, swing-and-miss is a power marker, not a power tax. The
# suppressor intuition is real for HITS, and models_hit.py carries it with the
# negative sign the data actually supports there.
M1_WEIGHTS = dict(barrel=0.18, hardhit=0.18, ev=0.17, xslg=0.16,
                  whiff=0.12, k=0.09, la=0.09)

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
    """Share of pitches thrown in each family, normalized to sum to 1.

    Savant's arsenal board is per-pitch usage percentages that do not always
    total 100 (rarely-thrown pitches are dropped from the board), so dividing
    by a hard 100 quietly scaled the whole matchup term down for those
    pitchers. Normalize by the actual total instead; an all-zero arsenal (no
    board match) yields zero usage and an inert, not negative, matchup term.
    """
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

# Coefficients are on z-scored inputs (except intercept), estimated by
# regularized logistic regression over data/backtest.csv and selected on a
# time-based holdout (fit_model.py reproduces them; C=0.03, holdout AUC 0.576).
#
# The feature set is deliberately short. barrel/xslg/xwoba/hardhit are 0.66-0.92
# correlated with each other, so a joint fit over all of them buys nothing on a
# holdout and produces coefficients that are only valid *together* — the old fit
# put xwoba at -0.198 purely to cancel xslg at +0.278. That is survivable inside
# one model and fatal the moment a caller copies a subset of the coefficients
# out, which is exactly what predict_today used to do. Keeping only features the
# prediction path actually reads makes that class of bug unrepresentable.
M3 = dict(
    intercept=math.log(LEAGUE["hr_per_pa"] / (1 - LEAGUE["hr_per_pa"])),  # baseline log-odds
    barrel=0.088, xslg=0.096, hardhit=0.147, la=0.111, k=0.049,
    # Unfit prior. model2_matchup is a raw score, not a z-score; the multiplier
    # is set to roughly half M1's measured per-SD slope so an extreme matchup
    # can move a hitter without dominating the measured terms.
    matchup=0.20,
)

# Which M3 keys are hitter features (everything else is intercept/matchup).
# predict_today rewrites both this and M3 together when a fit is available, so a
# fitted model is always installed whole or not at all.
M3_FEATURE_KEYS = ("barrel", "xslg", "hardhit", "la", "k")

# Pitcher-side terms. The fit estimates coefficients on the opposing starter's
# own contact-quality profile, but nothing used to read them back — the whole
# pitcher half of the fitted model was discarded at predict time and the only
# opponent signal left was the hand-rolled matchup score. These default to 0 so
# an unfitted model behaves exactly as before; predict_today fills them in.
M3_PITCHER = dict(p_barrel=0.0, p_hardhit=0.0, p_xslg=0.0,
                  p_xwoba=0.0, p_k=0.0, p_whiff=0.0)

# Pitcher features are z-scored against the moments of the PITCHER population
# the coefficients were fit on, which is a different population from the
# hitters. predict_today installs them; empty means the terms stay inert.
PITCHER_POP_STATS = None   # {p_field: (mean, sd)}


def _pitcher_z(field):
    """z-score for a pitcher feature ('p_barrel' -> PITCHER['barrel'])."""
    if not PITCHER_POP_STATS or field not in PITCHER_POP_STATS:
        return 0.0
    v = PITCHER.get(field[2:])
    if v is None:
        return 0.0
    mu, sd = PITCHER_POP_STATS[field]
    return (v - mu) / (sd or 1.0)


def model3_logistic(name):
    h = HITTERS[name]
    # Shrink only when a real batted-ball count came through (see shrink()), then
    # standardize on the same scale as every other feature. The old code z-scored
    # the shrunk value against the shrunk *lineup*, which is a third scale again
    # and left barrel% the one feature the fitted coefficient did not match.
    barrel_z = zscore("barrel", shrink(h.get("barrel"), h.get("bbe")))

    z = M3["intercept"] + M3.get("matchup", 0.0) * model2_matchup(name)
    for f in M3_FEATURE_KEYS:
        v = barrel_z if f == "barrel" else zscore(f, h.get(f))
        z += M3.get(f, 0.0) * v
    z += sum(c * _pitcher_z(f) for f, c in M3_PITCHER.items())
    return logistic(z)   # per-PA HR probability


# --------------------------------------------------------------------------
# LEAGUE BASELINE REFRESH
# --------------------------------------------------------------------------

# True once a fitted-coefficient file has supplied M3's intercept, so a later
# baseline refresh does not overwrite it.
M3_INTERCEPT_FITTED = False


def refresh_baseline():
    """Re-derive baseline-dependent constants after LEAGUE has been swapped in.

    ingest_live reads the real league rate out of data/base_rates.json and hands
    it over in the state dict, but M3's intercept is computed at import time
    from data.py's placeholder. Rebinding the module's LEAGUE therefore left the
    logistic anchored to the placeholder while _calibrate used the live rate —
    two different baselines inside one model. Callers that swap LEAGUE must call
    this; predict_today does.
    """
    if not M3_INTERCEPT_FITTED:
        M3["intercept"] = logit(LEAGUE["hr_per_pa"])


# --------------------------------------------------------------------------
# MODEL 4 — Context / Leverage Multiplier
#   Not a standalone predictor — a multiplier on Model 3. Captures times-through-
#   the-order decay (starter gets hit harder 3rd time) and lineup-slot PA volume.
# --------------------------------------------------------------------------

# Mean of the raw TTO ladder over a nine-man lineup.
TTO_MEAN = sum(1.0 + max(0, (4 - s)) * 0.04 for s in range(9)) / 9.0


def model4_context(name, lineup_order, park_factor=1.0, platoon_factor=1.0):
    try:
        slot = lineup_order.index(name)  # 0-indexed
    except ValueError:
        slot = 4
    # Top of order sees the starter more times and gets the 3rd-TTO penalty pitch.
    # Times-through-order lift, normalized to average 1.0 across a nine-man
    # lineup. The raw ladder (1.16 at leadoff down to 1.00 from slot 5) only
    # ever multiplies UP, so applying it to all nine slots added 4.4% to every
    # slate's expected output — a level bias that calibration then had to
    # absorb as if it were spread. Dividing by the ladder's own mean keeps the
    # slot-to-slot ordering and makes the term redistribute instead of inflate.
    tto_bonus = (1.0 + max(0, (4 - slot)) * 0.04) / TTO_MEAN
    combined_mult = tto_bonus * park_factor * platoon_factor
    # Expected PAs by slot scales opportunity (used by sim, returned for transparency)
    exp_pa = 4.6 - slot * 0.12
    return dict(tto_mult=combined_mult, exp_pa=exp_pa,
                park_factor=park_factor, platoon_factor=platoon_factor)


# --------------------------------------------------------------------------
# MODEL 5 — Ensemble with shrinkage
#   Blends M1 (linear), M2 (matchup), M3 (logistic) into one per-PA probability.
#   M1/M2 are scores, not probabilities, so they're squashed through a logistic
#   centered on the population before blending. M3 is already a probability.
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
M1_SLOPE = 0.466   # per unit of the M1 composite (HR label)
M2_SLOPE = 0.241   # per unit of the M2 matchup score

# CALIBRATION — a Platt map on the ensemble's log-odds distance from baseline:
#
#     logit(p_cal) = logit(base) + SHIFT + DAMP * (logit(p) - logit(base))
#
# DAMP < 1 narrows the spread, SHIFT moves the whole slate. Both are meant to
# be *estimated* from scored predictions (calibrate.py fits them off
# data/calibration_log.csv and writes models_out/calibration_hr.json); the
# values below are the fallback when no fit exists.
#
# The old code treated DAMP as "confidence in the coefficients" and
# predict_today set it to 1.0 the moment a fitted-coefficient file appeared, on
# the theory that a fitted model needs no damping. It does: the M3 coefficients
# are only one of three ensemble members, and the two heuristic members plus the
# matchup term are not on any fitted scale. Scored against real games that gave
# a top decile predicting 37% HR/game against 21% actual, and an overall Brier
# worse than always guessing the league rate. DAMP is a calibration parameter,
# not a confidence dial — leave it to the data.
# Fitted on the train window and checked on a held-out later window
# (see calibrate.py). Holdout log-loss is flat between 0.60 and 0.75 for all
# three targets and rises either side, so 0.70 is the shallow optimum rather
# than a tuned-to-the-decimal number. At this setting every target beats a
# constant league-rate prediction on both Brier and log-loss; the shipped model
# used to lose to that baseline on HR.
CALIBRATION_DAMP = 0.70    # slope on the log-odds deviation from baseline
CALIBRATION_SHIFT = 0.0    # constant log-odds offset applied after the slope

# Hard ceiling on a per-PA probability. Not an active constraint on a calibrated
# model (the best HR/PA season on record is ~0.10) — a guard so a bad feature
# join can never publish an impossible number. The old cap was 0.5.
P_MAX = 0.15

def _calibrate(p):
    base = LEAGUE["hr_per_pa"]
    lo_b = logit(base)
    return logistic(lo_b + CALIBRATION_SHIFT + CALIBRATION_DAMP * (logit(p) - lo_b))

def _score_to_prob(score, slope):
    """Map a raw model score to a probability anchored at the league baseline."""
    return logistic(logit(LEAGUE["hr_per_pa"]) + slope * score)

def model5_ensemble(name, lineup_order, park_factor=1.0, platoon_factor=1.0):
    p_lin = _score_to_prob(model1_linear(name), M1_SLOPE)
    p_mat = _score_to_prob(model2_matchup(name), M2_SLOPE)
    p_log = model3_logistic(name)

    p = (ENSEMBLE_W["logistic"] * p_log +
         ENSEMBLE_W["matchup"]  * p_mat +
         ENSEMBLE_W["linear"]   * p_lin)

    ctx = model4_context(name, lineup_order, park_factor=park_factor,
                         platoon_factor=platoon_factor)
    # Park, platoon and times-through-order are rate multipliers, so apply them
    # as an odds ratio rather than scaling the probability directly. The two
    # agree while p is small and diverge exactly where the old code did damage:
    # multiplying an already-hot 0.25 by 1.12 pushed it further out of range,
    # whereas the odds form stays bounded below 1.
    p_adj = logistic(logit(_calibrate(p)) + math.log(max(1e-6, ctx["tto_mult"])))
    p_adj = min(P_MAX, p_adj)
    return dict(p_per_pa=p_adj, exp_pa=ctx["exp_pa"],
                components=dict(logistic=p_log, matchup=p_mat, linear=p_lin),
                tto_mult=ctx["tto_mult"],
                park_factor=ctx["park_factor"],
                platoon_factor=ctx["platoon_factor"])
