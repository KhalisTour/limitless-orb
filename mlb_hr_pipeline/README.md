# HR-Likelihood Predictor — MVP Design Doc

A runnable Python MVP that predicts home-run likelihood for each hitter in a
lineup against a specific starting pitcher, then Monte-Carlos a full game 1,000×
to find which HR outcomes cluster. Built toward a real predictor: the structure,
shrinkage, and calibration hooks are all here; only the *coefficient fitting*
step is stubbed (it needs labeled outcome data you don't have yet).

```
data.py     transcribed table data (hitters, pitcher, lineup, league baselines)
models.py   the five models + shrinkage + calibration
sim.py      Monte Carlo full-game lineup simulator
```
Run: `python sim.py`

---

## At-a-glance: how the pipeline solves the problem

The question "who is likelier than baseline to homer in this outing?" is a
**per-plate-appearance binary-outcome** problem embedded in a **sequence** (a
game is ~38 PAs dealt around a 9-batter order). So the MVP does two things:

1. **Estimate a per-PA HR probability for each hitter** against this pitcher.
   Five models contribute; they are blended into one number per hitter.
2. **Simulate the sequence** — deal out a full game of PAs as Bernoulli trials
   on those probabilities, 1,000 times, and measure not just *how often* each
   hitter homers but *which homers co-occur* (back-to-back, multi-HR innings,
   multi-HR games).

Every model returns a per-PA probability (or a score mapped to one) so they're
directly comparable and feed one shared simulator.

---

## The five models

### Model 1 — Linear Composite "Barrel Expectancy"
**What it measures:** a hitter's standalone power-contact quality.
**Why this form:** a weighted sum of z-scored HR-antecedent stats is the
simplest defensible estimator and the most interpretable — you can read off
exactly why a hitter scores high. It's the baseline every other model is
judged against.
**Data it needs:** barrel%, xSLG, hardhit%, EV, K%, LA, whiff%.

> **Abstract.** Let **x** be a hitter's vector of standardized features,
> z_i = (v_i − μ_i)/σ_i, with population moments taken over the lineup. The score
> is S₁ = **wᵀz**, weights **w** chosen to reflect each feature's marginal
> association with HR (barrel highest, K%/whiff entering negatively as
> opportunity suppressors). Because z-scoring removes units, **w** is a pure
> mixing vector and S₁ is dimensionless, centered near 0 for a population-average
> hitter. It is a first-order (additive) approximation that ignores interactions
> — its weakness and the reason Model 2 exists.

### Model 2 — Matchup Interaction (pitch-family × hitter geometry)
**What it measures:** the *overlap* between what the pitcher throws and what the
hitter punishes. A great barrel rate against a pitch you never see is worthless.
**Why this form:** **multiplicative** across pitch families, because mismatches
compound — damage only counts in proportion to how often the pitcher throws
into it. Bibee is 51% elevated hard stuff (4-seam + cutter), so hitters who lift
and rarely whiff up are rewarded; chasers who whiff on offspeed are penalized on
his 30% change/curve.
**Data it needs:** pitcher arsenal %, pitcher barrel% allowed; hitter barrel, LA,
whiff, topped%, chase%.

> **Abstract.** Partition the arsenal into families F with usage weights u_f
> (Σu_f = 1). For each family define a hitter-damage functional D_f(**z**) —
> a small linear form over the features most relevant to that family
> (lift & contact vs rise; ground-suppression vs sink; discipline vs soft). The
> matchup score is S₂ = (Σ_f u_f · D_f) · (1 + β_p), where β_p =
> (barrel%_allowed − league)/league is the pitcher's signed vulnerability. The
> product term is what makes this an *interaction* model: hitter strength and
> pitcher usage multiply rather than add.

### Model 3 — Logistic Per-PA HR Probability (the workhorse)
**What it measures:** the actual probability of a HR in one PA.
**Why this form:** HR/PA is a Bernoulli outcome, so the natural model is logistic
regression — it maps any real-valued linear predictor to (0,1) and its
coefficients are log-odds, the correct scale for combining evidence. The
intercept is pinned to the league baseline (~3.2% HR/PA) so an average hitter vs
an average pitcher comes out at baseline by construction.
**Data it needs:** shrunk barrel%, xSLG, hardhit%, LA, whiff%, and the Model 2
matchup score as a feature.

> **Abstract.** P(HR) = σ(η), σ the logistic function, η = β₀ + **βᵀz** +
> β_m·S₂. β₀ = logit(league HR/PA) anchors the baseline. Barrel enters
> **shrunk** (see below) and re-standardized so small-sample players don't
> dominate the log-odds. The link is canonical for Bernoulli data; coefficients
> are currently *reasoned priors* — fitting them by maximum likelihood on
> labeled PA outcomes is the single step that turns this MVP into a trained
> predictor. See "Data integrity."

### Model 4 — Context / Leverage Multiplier
**What it measures:** sequence effects the contact stats can't see — times-
through-the-order decay (a starter is hit harder the 3rd time through as velo
dips) and lineup-slot PA volume.
**Why this form:** a **multiplier** on Model 3, not a standalone predictor,
because context scales an existing probability rather than generating one.
**Data it needs:** batting order only (PA volume and TTO follow from slot).

> **Abstract.** P_context = P · m(slot), with m = 1 + max(0, 4−slot)·c giving
> top-of-order bats up to a ~12% lift for their extra 3rd-TTO look. Expected PAs
> e(slot) = a − b·slot feeds the simulator's opportunity weighting. This is the
> crudest model and the first place to add real TTO splits.

### Model 5 — Ensemble with shrinkage (what the sim actually uses)
**What it measures:** the consensus per-PA probability, robust to any single
model's blind spot.
**Why this form:** M1/M2 are scores and M3 is a probability, so the scores are
squashed through a baseline-anchored logistic before a weighted blend
(0.45 logistic / 0.30 matchup / 0.25 linear). Then Model 4's multiplier and a
calibration damp are applied.
**Data it needs:** outputs of M1–M4.

> **Abstract.** P_ens = Σ_k w_k P_k, P_k each a probability on a common baseline
> anchor, then × m(slot), then calibrated. Ensembling reduces variance from the
> hand-set coefficients; the weights express trust (logistic > matchup > linear).

---

## Shrinkage — why low-BBE players don't blow up

A rate computed off 64 batted balls (Langford) is far noisier than one off 186
(Jung). Empirical-Bayes shrinkage pulls each rate toward a prior by a weight
w = BBE/(BBE+k): at k=150, a 64-BBE player is ~30% his own rate / 70% prior; a
186-BBE player is ~55/45. This is why the MVP refuses to crown a small-sample
hot streak.

---

## Monte Carlo: which outcomes cluster

`sim.py` deals 38 PAs per game around the order, each a Bernoulli draw on the
hitter's Model-5 probability, ×1,000 games. It records co-occurrence:

- **Back-to-back HR** (adjacent PAs both homer)
- **Two HR within 9 PAs** (rough "same-inning-ish" cluster)
- **Multi-HR games** per player, and the **team HR distribution**

**Representative 1,000-game output (calibrated):**

| Hitter | P(HR)/PA | HR/game | P(≥1 HR) | P(multi) |
|---|---|---|---|---|
| Corey Seager | .061 | .306 | .269 | .033 |
| Brandon Nimmo | .065 | .291 | .254 | .037 |
| Joc Pederson | .039 | .202 | .185 | .017 |
| Kyle Higashioka | .039 | .144 | .139 | .005 |
| Jake Burger | .031 | .116 | .109 | .007 |
| Josh Jung | .030 | .106 | .105 | .001 |

Most likely to occur **consecutively / in proximity:** the clustering is driven
by the top-of-order bats — Seager and Nimmo bat 2nd and 4th, so the most common
proximate outcome is a Seager/Nimmo pair within a 9-PA window (~0.37 games show
a 2-HR cluster). True back-to-back is rare (~0.05/game). The team total centers
on **1–2 HR per game**, with ~24% shutout games — a realistic shape.

---

## Data integrity — honest caveats

The data and the model are structurally aligned: every feature the models use is
present in your tables, and every model outputs the per-PA quantity the simulator
consumes. But three integrity gaps matter and are flagged in code:

1. **Coefficients are reasoned, not fitted.** M1's weights and M3's logistic
   coefficients are priors, not estimated from labeled HR outcomes. That's why
   raw output ran *hot* (Seager at ~15% HR/PA) until a `CALIBRATION_DAMP` was
   applied to compress deviations toward the baseline. The damp is a placeholder
   — with a real PA-outcome dataset you delete it and fit M3 by maximum
   likelihood. **Trust the rankings; treat the absolute probabilities as
   provisional.**
2. **Sample size varies 64–186 BBE.** Handled by shrinkage, but Langford and
   Higashioka (64–69 BBE) remain the least reliable rows.
3. **Missing the biggest real variable: handedness/platoon.** Your tables have no
   batter/pitcher handedness or Bibee's platoon splits — the factor most likely
   to reshuffle the order. Plate-discipline fields are also absent for the bottom
   three hitters (defaulted to population means).

Net: the pipeline has integrity *to itself* — inputs, models, and the simulated
outcome are consistent and the relative ordering is stable across 1,000 sims.
It does **not** yet have integrity to ground truth, because no coefficient has
met a real outcome. The fix is one well-scoped step: fit M3 on historical PA
data and drop the calibration damp.
