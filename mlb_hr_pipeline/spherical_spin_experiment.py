"""
Spherical spin experiment — Secondary Target #4.

Maps pitch movement profiles (pfx_x, pfx_z) onto S² and computes
spherical harmonic features Y_lm for l=0,1,2 (9 total). Tests whether
adding these features to the CP tensor model improves HR prediction.

Requires: data/pitches_enriched.csv with pfx_x, pfx_z columns.
Run pull_enriched.py first.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import sph_harm
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss

from tensor_model import CPContactModel, PITCH_FAMILIES, ZONE_ORDER, COUNT_ORDER

REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"


def movement_to_spherical(pfx_x, pfx_z):
    """Map pitch movement (pfx_x, pfx_z) in inches to (theta, phi) on S².

    theta ∈ [0, π]: polar angle from the "pure rise" pole
    phi ∈ [0, 2π): azimuthal angle around the movement plane

    Convention:
      - Pure backspin (rise): theta ≈ 0
      - Pure topspin (drop): theta ≈ π
      - Pure arm-side run: phi ≈ π/2
      - Pure glove-side cut: phi ≈ 3π/2
    """
    r = np.sqrt(pfx_x**2 + pfx_z**2)
    r_max = 25.0  # ~max movement in inches for normalization

    # theta: map movement magnitude to polar angle
    # Zero movement → equator (θ=π/2), max movement → poles
    # Positive pfx_z (rise) → θ<π/2, negative (drop) → θ>π/2
    theta = np.pi / 2 - np.arctan2(pfx_z, r_max / 2)
    theta = np.clip(theta, 0.01, np.pi - 0.01)

    # phi: azimuthal direction of movement
    phi = np.arctan2(pfx_x, np.abs(pfx_z) + 1e-6) % (2 * np.pi)

    return theta, phi


def compute_spherical_harmonics(theta, phi, l_max=2):
    """Compute real spherical harmonics Y_lm(theta, phi) for l=0..l_max.

    Returns array of shape (n, n_features) where n_features = (l_max+1)^2.
    For l_max=2: 9 features (l=0: 1, l=1: 3, l=2: 5).
    """
    n = len(theta)
    features = []
    col_names = []

    for l in range(l_max + 1):
        for m in range(-l, l + 1):
            # scipy sph_harm uses (m, l, phi, theta) convention
            # and returns complex values; we take real/imag parts
            ylm = sph_harm(abs(m), l, phi, theta)
            if m < 0:
                val = np.sqrt(2) * (-1)**m * ylm.imag
            elif m == 0:
                val = ylm.real
            else:
                val = np.sqrt(2) * (-1)**m * ylm.real
            features.append(val.real)
            col_names.append(f"Y_{l}_{m}")

    return np.column_stack(features), col_names


def collapse_enriched_to_pa(pitches):
    """Collapse enriched pitch data to per-PA, aggregating movement features."""
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
        hit_distance=("hit_distance_sc", "last"),
        # Movement of the final pitch
        last_pfx_x=("pfx_x", "last"),
        last_pfx_z=("pfx_z", "last"),
        # Mean movement across all pitches in PA (pitch mix signature)
        mean_pfx_x=("pfx_x", "mean"),
        mean_pfx_z=("pfx_z", "mean"),
        mean_spin_rate=("release_spin_rate", "mean"),
    )
    pa = pa[pa["events"].notna()].copy()
    pa["hr"] = (pa["events"] == "home_run").astype(int)
    return pa


def build_harmonic_features(pa):
    """Compute spherical harmonic features for each PA's final pitch movement."""
    mask = pa["last_pfx_x"].notna() & pa["last_pfx_z"].notna()
    pa_valid = pa[mask].copy()

    theta, phi = movement_to_spherical(
        pa_valid["last_pfx_x"].values,
        pa_valid["last_pfx_z"].values,
    )
    ylm_features, ylm_names = compute_spherical_harmonics(theta, phi, l_max=2)

    for i, name in enumerate(ylm_names):
        pa_valid[name] = ylm_features[:, i]

    return pa_valid, ylm_names


def main():
    enriched_path = DATA_DIR / "pitches_enriched.csv"
    if not enriched_path.exists():
        print(f"ERROR: {enriched_path} not found.", file=sys.stderr)
        print("Run pull_enriched.py first to fetch movement data.", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print("SPHERICAL SPIN EXPERIMENT — S² Harmonic Features for HR Prediction")
    print("=" * 70)

    print("\n[1/5] Loading enriched pitch data...")
    pitches = pd.read_csv(enriched_path)
    print(f"  {len(pitches):,} pitches, {len(pitches.columns)} columns")

    pfx_present = "pfx_x" in pitches.columns and "pfx_z" in pitches.columns
    if not pfx_present:
        print("ERROR: pfx_x / pfx_z not in data. Cannot run experiment.")
        sys.exit(1)
    pfx_valid = pitches["pfx_x"].notna() & pitches["pfx_z"].notna()
    print(f"  Movement data: {pfx_valid.sum():,} pitches ({pfx_valid.mean():.1%})")

    print("\n[2/5] Collapsing to PAs and computing S² harmonics...")
    pa = collapse_enriched_to_pa(pitches)
    pa_ylm, ylm_names = build_harmonic_features(pa)
    print(f"  PAs with harmonic features: {len(pa_ylm):,}")
    print(f"  Harmonic features: {ylm_names}")

    print("\n[3/5] Preparing CP model features...")
    from tensor_experiment import prepare_features, temporal_split, evaluate

    # Standard CP features
    X_cp, y_cp, dates_cp, _ = prepare_features(pa_ylm)

    # Add harmonic features as auxiliary continuous inputs
    ylm_data = pa_ylm[ylm_names].values

    # We need to align indices: prepare_features may drop rows
    pa_ylm_reset = pa_ylm.reset_index(drop=True)
    X_cp_full, y_full, dates_full, pa_clean = prepare_features(pa_ylm_reset)
    ylm_aligned = pa_clean[ylm_names].values

    X_train, y_train, X_test, y_test = temporal_split(
        X_cp_full, y_full, dates_full
    )

    # Split harmonic features in sync
    unique_dates = sorted(dates_full.unique())
    n_train_dates = int(len(unique_dates) * 0.8)
    cutoff = unique_dates[n_train_dates]
    train_mask = dates_full < cutoff
    ylm_train = ylm_aligned[train_mask.values]
    ylm_test = ylm_aligned[~train_mask.values]

    print(f"  Train: {len(y_train):,} PAs, Test: {len(y_test):,} PAs")

    print("\n[4/5] Training models...")
    results = {}

    # Model A: CP only (no harmonics) — retrain on enriched data
    print("\n  --- CP R=3 (no harmonics, baseline) ---")
    t0 = time.time()
    model_base = CPContactModel(rank=3, reg=0.01, reg_player_mult=10.0,
                                max_iter=300, seed=42)
    model_base.fit(X_train, y_train, X_val=X_test, y_val=y_test, verbose=True)
    pred_base = model_base.predict_proba(X_test)
    results["cp_r3_no_harmonics"] = evaluate(y_test, pred_base, "CP R=3 (no harmonics)")

    # Model B: CP + Y_lm features via logistic regression on CP score + harmonics
    print("\n  --- CP R=3 + S² harmonics (hybrid) ---")
    from sklearn.linear_model import LogisticRegression

    # Get CP scores as a feature, then stack with harmonics
    cp_scores_train = model_base.predict_proba(X_train)
    cp_scores_test = model_base.predict_proba(X_test)

    hybrid_train = np.column_stack([
        np.log(cp_scores_train / (1 - cp_scores_train + 1e-15)),  # logit of CP pred
        ylm_train,
    ])
    hybrid_test = np.column_stack([
        np.log(cp_scores_test / (1 - cp_scores_test + 1e-15)),
        ylm_test,
    ])

    # Standardize
    mu = hybrid_train.mean(axis=0)
    sd = hybrid_train.std(axis=0)
    sd[sd == 0] = 1.0
    hybrid_train_z = (hybrid_train - mu) / sd
    hybrid_test_z = (hybrid_test - mu) / sd

    clf = LogisticRegression(max_iter=1000, C=1.0)
    clf.fit(hybrid_train_z, y_train)
    pred_hybrid = clf.predict_proba(hybrid_test_z)[:, 1]
    results["cp_r3_plus_harmonics"] = evaluate(y_test, pred_hybrid,
                                               "CP R=3 + S² harmonics")

    # Report harmonic feature importances
    feature_names = ["cp_logit"] + ylm_names
    coefs = clf.coef_[0]
    print(f"\n  Hybrid model coefficients:")
    for name, coef in sorted(zip(feature_names, coefs), key=lambda x: abs(x[1]),
                             reverse=True):
        print(f"    {name:<12s}  {coef:+.4f}")

    print("\n[5/5] Summary")
    print("=" * 70)
    print(f"{'Model':<35s}  {'AUC':>8s}  {'Brier':>10s}  {'LogLoss':>10s}")
    print("-" * 68)
    for name, m in results.items():
        print(f"{name:<35s}  {m['auc']:>8.4f}  {m['brier']:>10.5f}  "
              f"{m['log_loss']:>10.5f}")

    delta_auc = (results["cp_r3_plus_harmonics"]["auc"]
                 - results["cp_r3_no_harmonics"]["auc"])
    print(f"\nΔAUC from harmonics: {delta_auc:+.4f}")
    if delta_auc > 0.005:
        print("→ Spherical harmonic features IMPROVE discrimination.")
    elif delta_auc < -0.005:
        print("→ Spherical harmonic features HURT — likely noise or overfitting.")
    else:
        print("→ Marginal effect — movement direction doesn't add much over pitch_type.")

    out_path = DATA_DIR / "spherical_spin_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
