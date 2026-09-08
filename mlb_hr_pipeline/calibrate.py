"""
Phase 5 — Estimate the calibration constants from scored predictions.

models.py, models_xbh.py and models_hit.py each end with a Platt map on the
ensemble's log-odds distance from the league baseline:

    logit(p_cal) = logit(base) + CALIBRATION_SHIFT
                              + CALIBRATION_DAMP * (logit(p) - logit(base))

DAMP controls spread, SHIFT controls level. They are parameters of the model and
have to be *measured*, which is what this script does: it reads the scored
predictions in data/calibration_log.csv, fits (DAMP, SHIFT) by maximum
likelihood against what actually happened, and writes the result to
models_out/calibration_<target>.json for predict_today to install.

The failure this replaces: predict_today set CALIBRATION_DAMP = 1.0 whenever a
fitted-coefficient file existed, reasoning that a fitted model needs no damping.
Scored against 3,771 real predictions, that produced a top decile forecasting
37.2% HR-per-game against 20.7% actual, a +24.5% overall bias, and a Brier score
(0.11096) worse than predicting the league rate for every hitter (0.10830).
Nothing in the pipeline was watching for that, because nothing compared the
published numbers to results per target.

A fit is only meaningful for the model generation that produced the logged rows,
so each output records the number of rows and the date range behind it and
predict_today refuses to apply a calibration fit from fewer than MIN_ROWS.

Run: python calibrate.py [target ...]      (default: all three)
"""

import json
import sys
import traceback
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score

REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
MODELS_DIR = REPO / "models_out"
MODELS_DIR.mkdir(exist_ok=True)

LOG_PATH = DATA_DIR / "calibration_log.csv"

# Below this many scored rows the fit is noise and the shipped defaults win.
MIN_ROWS = 750
# Fraction of the date range held back to check the fit generalizes.
HOLDOUT_FRAC = 0.25

TARGETS = {
    #  target: (per-game prob column, per-PA prob column, outcome column, base rate key)
    "hr":  ("p_hr", "p_per_pa", "actual_hr", "hr_per_pa"),
    "xbh": ("p_xbh", "p_xbh_per_pa", "actual_xbh", "xbh_per_pa"),
    "hit": ("p_hit", "p_hit_per_pa", "actual_hit", "hit_per_pa"),
}


def _logit(p, eps=1e-9):
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return np.log(p / (1 - p))


def _base_rates():
    p = DATA_DIR / "base_rates.json"
    rates = {"hr_per_pa": 0.032, "xbh_per_pa": 0.076, "hit_per_pa": 0.218}
    if p.exists():
        br = json.loads(p.read_text())
        for k in rates:
            if br.get(k):
                rates[k] = float(br[k])
    return rates


def _apply(p_pa, base, damp, shift, exp_pa):
    """Recalibrate a per-PA probability, then lift it to a per-game one."""
    lb = float(np.log(base / (1 - base)))
    z = lb + shift + damp * (_logit(p_pa) - lb)
    p = 1.0 / (1.0 + np.exp(-z))
    return 1.0 - (1.0 - p) ** np.asarray(exp_pa, dtype=float)


def calibrate_target(target: str, log: pd.DataFrame, base: float, generation: int):
    p_game_col, p_pa_col, y_col, _ = TARGETS[target]
    need = [p_pa_col, y_col, "exp_pa", "game_date"]
    if any(c not in log.columns for c in need):
        print(f"[calibrate] {target}: log has no {p_pa_col}/{y_col} column yet — "
              f"run score.py on a slate predicted by the current pipeline first")
        return None
    df = log[need + (["model_generation"] if "model_generation" in log.columns
                      else [])].dropna(subset=need).copy()

    # Constants fitted to one model generation must not be applied to another:
    # the correction would land on top of a model that no longer produces those
    # numbers. Rows from earlier generations are dropped rather than blended.
    if "model_generation" in df.columns:
        n_all = len(df)
        df = df[df["model_generation"] == generation]
        if len(df) < n_all:
            print(f"[calibrate] {target}: dropped {n_all - len(df)} rows from "
                  f"other model generations ({len(df)} at generation {generation})")
    else:
        print(f"[calibrate] {target}: log carries no model_generation column, so "
              f"every row predates the current models — nothing to fit against")
        return None

    if len(df) < MIN_ROWS:
        print(f"[calibrate] {target}: only {len(df)} scored rows (need {MIN_ROWS}) "
              f"— keeping the shipped defaults")
        return None

    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.sort_values("game_date")
    cut = df["game_date"].quantile(1 - HOLDOUT_FRAC)
    tr, te = df[df.game_date <= cut], df[df.game_date > cut]
    if len(te) < 100:
        tr, te = df, df
        print(f"[calibrate] {target}: too few late rows to hold out; reporting in-sample")

    lb = float(np.log(base / (1 - base)))

    # DAMP and SHIFT live on the per-PA scale but the label is per-GAME, so they
    # cannot be read off an ordinary logistic regression: its intercept would
    # absorb the per-PA-to-per-game lift and come back enormous. Minimize the
    # per-game log-loss directly over the two per-PA parameters instead.
    d_tr = _logit(tr[p_pa_col].values) - lb
    y_tr = tr[y_col].values.astype(int)
    epa_tr = tr["exp_pa"].fillna(4.2).values

    def _nll(params):
        damp_, shift_ = params
        p_pa = 1.0 / (1.0 + np.exp(-(lb + shift_ + damp_ * d_tr)))
        p_g = np.clip(1.0 - (1.0 - p_pa) ** epa_tr, 1e-9, 1 - 1e-9)
        return -float(np.mean(y_tr * np.log(p_g) + (1 - y_tr) * np.log(1 - p_g)))

    best, grid = None, None
    for damp_ in np.linspace(0.0, 2.0, 41):
        for shift_ in np.linspace(-0.6, 0.6, 49):
            v = _nll((damp_, shift_))
            if best is None or v < best:
                best, grid = v, (damp_, shift_)
    try:
        from scipy.optimize import minimize
        res = minimize(_nll, np.array(grid), method="Nelder-Mead")
        damp, shift = (float(res.x[0]), float(res.x[1])) if res.success else grid
    except ImportError:
        damp, shift = grid
    damp, shift = float(damp), float(shift)

    y = te[y_col].values.astype(int)
    epa_te = te["exp_pa"].fillna(4.2).values
    rows = [("as-logged (damp=1)", 1.0, 0.0,
             _apply(te[p_pa_col].values, base, 1.0, 0.0, epa_te)),
            ("fitted", damp, shift,
             _apply(te[p_pa_col].values, base, damp, shift, epa_te))]

    print(f"\n[calibrate] {target}: {len(df)} rows, "
          f"{df.game_date.min().date()} -> {df.game_date.max().date()}; "
          f"train {len(tr)} / holdout {len(te)}  (base {base:.4f})")
    base_p = np.full(len(y), tr[y_col].mean())
    print(f"  {'config':<20}{'damp':>7}{'shift':>8}{'bias':>9}{'brier':>10}{'logloss':>10}")
    print(f"  {'constant baseline':<20}{'—':>7}{'—':>8}{'—':>9}"
          f"{brier_score_loss(y, base_p):>10.5f}{log_loss(y, base_p, labels=[0,1]):>10.5f}")
    for label, d_, s_, p in rows:
        print(f"  {label:<20}{d_:>7.3f}{s_:>+8.3f}"
              f"{(p.mean()/max(y.mean(),1e-9)-1)*100:>+8.1f}%"
              f"{brier_score_loss(y, p):>10.5f}{log_loss(y, p, labels=[0,1]):>10.5f}")

    p_fit = rows[-1][3]
    out = {
        "target": target,
        "fit_at": dt.datetime.utcnow().isoformat() + "Z",
        "model_generation": generation,
        "base_rate": base,
        "damp": damp,
        "shift": shift,
        "n_rows": int(len(df)),
        "date_range": [str(df.game_date.min().date()), str(df.game_date.max().date())],
        "holdout": {
            "n": int(len(te)),
            "brier": float(brier_score_loss(y, p_fit)),
            "log_loss": float(log_loss(y, p_fit, labels=[0, 1])),
            "baseline_brier": float(brier_score_loss(y, base_p)),
            "baseline_log_loss": float(log_loss(y, base_p, labels=[0, 1])),
            "auc": float(roc_auc_score(y, p_fit)) if len(set(y)) >= 2 else None,
        },
    }
    out["holdout"]["beats_baseline"] = (
        out["holdout"]["log_loss"] < out["holdout"]["baseline_log_loss"])

    path = MODELS_DIR / f"calibration_{target}.json"
    if not out["holdout"]["beats_baseline"]:
        # Refuse to install a calibration that scores worse than predicting the
        # league rate for everyone. A calibration file is applied blindly by
        # predict_today, so writing one that loses to the trivial forecast would
        # reintroduce exactly the failure this script exists to catch. The usual
        # cause is a log written by an older model generation: the fitted
        # constants describe a model that is no longer running.
        print(f"  REFUSED: the fitted {target} calibration does not beat a constant "
              f"league-rate prediction on the holdout "
              f"({out['holdout']['log_loss']:.5f} vs "
              f"{out['holdout']['baseline_log_loss']:.5f}). Not written; the "
              f"shipped defaults stay in place. If the log predates the current "
              f"models, re-score a few slates and re-run.")
        if path.exists():
            path.unlink()
            print(f"  removed stale {path.name}")
        return None
    path.write_text(json.dumps(out, indent=2))
    print(f"  wrote {path}")
    return out


def main(targets=None):
    if not LOG_PATH.exists():
        raise RuntimeError(f"Missing {LOG_PATH}. Run score.py on some past slates first.")
    log = pd.read_csv(LOG_PATH)
    rates = _base_rates()
    import models
    generation = models.MODEL_GENERATION
    print(f"[calibrate] fitting against model generation {generation}")
    for t in (targets or list(TARGETS)):
        if t not in TARGETS:
            raise SystemExit(f"unknown target {t!r}; choose from {list(TARGETS)}")
        calibrate_target(t, log, rates[TARGETS[t][3]], generation)


if __name__ == "__main__":
    try:
        main(sys.argv[1:] or None)
    except Exception:
        traceback.print_exc()
        print("\nRerun: python calibrate.py [hr|xbh|hit ...]", file=sys.stderr)
        sys.exit(1)
