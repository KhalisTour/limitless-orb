"""
End-to-end tensor decomposition experiment for HR prediction.

Loads pitches_2025.csv, collapses to per-PA, trains CP models at
R=1,3,5,10, and compares against baseline and M3 logistic on a
temporal 80/20 split.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
from sklearn.linear_model import LogisticRegression

from tensor_model import CPContactModel, PITCH_FAMILIES, FAMILY_ORDER, ZONE_ORDER, COUNT_ORDER

REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"


def collapse_pitches_to_pa(pitches):
    """Collapse pitch-level rows to one row per PA with the final pitch's attributes."""
    pitches = pitches.sort_values(
        ["game_pk", "at_bat_number", "inning", "balls", "strikes"]
    )
    grouped = pitches.groupby(["game_pk", "at_bat_number"], as_index=False)
    pa = grouped.agg(
        game_date=("game_date", "first"),
        batter=("batter", "first"),
        pitcher=("pitcher", "first"),
        events=("events", "last"),
        last_pitch_type=("pitch_type", "last"),
        last_zone=("zone", "last"),
        last_balls=("balls", "last"),
        last_strikes=("strikes", "last"),
        home_team=("home_team", "first"),
        launch_angle=("launch_angle", "last"),
        launch_speed=("launch_speed", "last"),
    )
    pa = pa[pa["events"].notna()].copy()
    pa["hr"] = (pa["events"] == "home_run").astype(int)
    return pa


def prepare_features(pa):
    """Build the 6-factor feature matrix for the CP model.

    Returns (X, y, mask) where mask indicates rows with all factors present.
    """
    pa = pa.copy()

    pa["pitch_family"] = pa["last_pitch_type"].map(PITCH_FAMILIES)

    pa["zone_int"] = pa["last_zone"].apply(
        lambda z: int(z) if pd.notna(z) and int(z) in ZONE_ORDER else np.nan
    )

    pa["count_str"] = pa.apply(
        lambda r: f"{int(r['last_balls'])}-{int(r['last_strikes'])}"
        if pd.notna(r["last_balls"]) and pd.notna(r["last_strikes"]) else np.nan,
        axis=1,
    )

    valid_counts = {f"{b}-{s}" for b, s in COUNT_ORDER}
    pa.loc[~pa["count_str"].isin(valid_counts), "count_str"] = np.nan

    mask = (
        pa["pitch_family"].notna()
        & pa["zone_int"].notna()
        & pa["count_str"].notna()
        & pa["home_team"].notna()
    )

    pa_clean = pa[mask].copy()

    X = pa_clean[
        ["batter", "pitcher", "pitch_family", "zone_int", "count_str", "home_team"]
    ].values
    y = pa_clean["hr"].values.astype(float)
    game_dates = pd.to_datetime(pa_clean["game_date"])

    return X, y, game_dates, pa_clean


def temporal_split(X, y, dates, train_frac=0.8):
    """Split by date: earliest 80% of dates -> train, rest -> test."""
    unique_dates = sorted(dates.unique())
    n_train_dates = int(len(unique_dates) * train_frac)
    cutoff = unique_dates[n_train_dates]
    train_mask = dates < cutoff
    test_mask = ~train_mask

    print(f"[split] cutoff date: {cutoff}")
    print(f"[split] train: {train_mask.sum():,} PAs "
          f"({y[train_mask.values].mean():.4f} HR rate)")
    print(f"[split] test:  {test_mask.sum():,} PAs "
          f"({y[test_mask.values].mean():.4f} HR rate)")

    return (
        X[train_mask.values], y[train_mask.values],
        X[test_mask.values], y[test_mask.values],
    )


def evaluate(y_true, y_pred, label=""):
    """Compute AUC, Brier, log-loss with bootstrap CIs."""
    metrics = {}

    try:
        metrics["auc"] = float(roc_auc_score(y_true, y_pred))
    except ValueError:
        metrics["auc"] = float("nan")
    metrics["brier"] = float(brier_score_loss(y_true, y_pred))
    metrics["log_loss"] = float(log_loss(y_true, y_pred))

    rng = np.random.RandomState(42)
    n = len(y_true)
    n_boot = 200
    boot_auc = []
    boot_brier = []
    boot_ll = []
    for _ in range(n_boot):
        idx = rng.choice(n, n, replace=True)
        yt = y_true[idx]
        yp = y_pred[idx]
        if yt.sum() == 0 or yt.sum() == n:
            continue
        boot_auc.append(roc_auc_score(yt, yp))
        boot_brier.append(brier_score_loss(yt, yp))
        boot_ll.append(log_loss(yt, yp))

    if boot_auc:
        metrics["auc_ci"] = [
            float(np.percentile(boot_auc, 2.5)),
            float(np.percentile(boot_auc, 97.5)),
        ]
        metrics["brier_ci"] = [
            float(np.percentile(boot_brier, 2.5)),
            float(np.percentile(boot_brier, 97.5)),
        ]
        metrics["log_loss_ci"] = [
            float(np.percentile(boot_ll, 2.5)),
            float(np.percentile(boot_ll, 97.5)),
        ]

    if label:
        auc_ci = metrics.get("auc_ci", [0, 0])
        print(f"  {label:<20s}  AUC={metrics['auc']:.4f} [{auc_ci[0]:.4f}, {auc_ci[1]:.4f}]  "
              f"Brier={metrics['brier']:.5f}  LogLoss={metrics['log_loss']:.5f}")

    return metrics


def calibration_table(y_true, y_pred, n_bins=10):
    """Return calibration bins: predicted vs actual."""
    bins = np.linspace(0, 1, n_bins + 1)
    table = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (y_pred >= lo) & (y_pred < hi)
        if hi == 1.0:
            mask |= (y_pred == 1.0)
        if mask.sum() == 0:
            continue
        table.append({
            "bin": f"[{lo:.2f}, {hi:.2f})",
            "n": int(mask.sum()),
            "pred_mean": float(y_pred[mask].mean()),
            "actual_mean": float(y_true[mask].mean()),
        })
    return table


def fit_baseline_logistic(X_train, y_train, X_test):
    """Fit a logistic regression on one-hot encoded factors as M3 surrogate."""
    factor_names = ["batter", "pitcher", "pitch_family", "zone", "count", "park"]

    all_X = np.vstack([X_train, X_test])
    vocabs = {}
    for i, name in enumerate(factor_names):
        vals = sorted(set(all_X[:, i]))
        vocabs[name] = {v: idx for idx, v in enumerate(vals)}

    small_factors = ["pitch_family", "zone", "count", "park"]
    feature_cols = []
    for name in small_factors:
        feature_cols.append((name, vocabs[name]))

    def encode(X):
        parts = []
        for name, vocab in feature_cols:
            i = factor_names.index(name)
            n_vals = len(vocab)
            ohe = np.zeros((len(X), n_vals))
            for j in range(len(X)):
                idx = vocab.get(X[j, i], -1)
                if idx >= 0:
                    ohe[j, idx] = 1.0
            parts.append(ohe)
        return np.hstack(parts)

    Xtr = encode(X_train)
    Xte = encode(X_test)

    clf = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
    clf.fit(Xtr, y_train)
    return clf.predict_proba(Xte)[:, 1]


def main():
    pitches_path = DATA_DIR / "pitches_2025.csv"
    if not pitches_path.exists():
        print(f"ERROR: {pitches_path} not found", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print("TENSOR CONTACT MODEL — CP DECOMPOSITION EXPERIMENT")
    print("=" * 70)

    print("\n[1/5] Loading and collapsing pitches to PAs...")
    pitches = pd.read_csv(pitches_path)
    print(f"  Loaded {len(pitches):,} pitches")
    pa = collapse_pitches_to_pa(pitches)
    print(f"  Collapsed to {len(pa):,} PAs, {int(pa['hr'].sum()):,} HRs "
          f"({pa['hr'].mean():.4f} HR rate)")

    print("\n[2/5] Preparing features...")
    X, y, dates, pa_clean = prepare_features(pa)
    print(f"  Valid PAs: {len(y):,} ({len(y)/len(pa):.1%} of total)")

    print("\n[3/5] Temporal train/test split...")
    X_train, y_train, X_test, y_test = temporal_split(X, y, dates)

    print("\n[4/5] Training models...")
    results = {}

    # Baseline: constant predictor (league HR rate)
    baseline_pred = np.full(len(y_test), y_train.mean())
    results["baseline_constant"] = evaluate(y_test, baseline_pred, "Baseline (constant)")

    # M3 surrogate: logistic on one-hot factors
    print("  Fitting M3 surrogate (logistic on factors)...")
    m3_pred = fit_baseline_logistic(X_train, y_train, X_test)
    results["m3_logistic"] = evaluate(y_test, m3_pred, "M3 Logistic")

    # CP models at various ranks — scale regularization with rank
    rank_configs = [
        (1,  0.005, 5.0),
        (3,  0.01,  10.0),
        (5,  0.02,  15.0),
        (10, 0.05,  20.0),
    ]
    for rank, reg, reg_player_mult in rank_configs:
        print(f"\n  --- CP Rank {rank} (reg={reg}, player_mult={reg_player_mult}) ---")
        t0 = time.time()
        model = CPContactModel(
            rank=rank,
            reg=reg,
            reg_player_mult=reg_player_mult,
            max_iter=300,
            seed=42,
        )
        model.fit(X_train, y_train, X_val=X_test, y_val=y_test, verbose=True)
        elapsed = time.time() - t0

        pred = model.predict_proba(X_test)
        metrics = evaluate(y_test, pred, f"CP R={rank}")
        metrics["train_time_s"] = round(elapsed, 1)
        metrics["reg"] = reg
        metrics["reg_player_mult"] = reg_player_mult
        results[f"cp_rank_{rank}"] = metrics

        model_path = DATA_DIR / f"cp_model_R{rank}.json"
        model.save(model_path)
        print(f"  Saved model to {model_path}")

    # Calibration for best CP model
    best_rank = max(
        [1, 3, 5, 10],
        key=lambda r: results[f"cp_rank_{r}"]["auc"]
    )
    print(f"\n  Best CP rank by AUC: R={best_rank}")

    best_model = CPContactModel.load(DATA_DIR / f"cp_model_R{best_rank}.json")
    best_pred = best_model.predict_proba(X_test)
    cal = calibration_table(y_test, best_pred)
    results[f"cp_rank_{best_rank}"]["calibration"] = cal

    print(f"\n  Calibration for CP R={best_rank}:")
    for row in cal:
        print(f"    {row['bin']:<16s}  n={row['n']:>5d}  "
              f"pred={row['pred_mean']:.4f}  actual={row['actual_mean']:.4f}")

    print("\n[5/5] Summary")
    print("=" * 70)
    print(f"{'Model':<22s}  {'AUC':>8s}  {'Brier':>10s}  {'LogLoss':>10s}")
    print("-" * 55)
    for name, m in results.items():
        print(f"{name:<22s}  {m['auc']:>8.4f}  {m['brier']:>10.5f}  {m['log_loss']:>10.5f}")
    print("=" * 70)

    # Save results
    out_path = DATA_DIR / "tensor_experiment_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
