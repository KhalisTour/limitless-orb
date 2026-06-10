"""
Distance residual model — Secondary Target #5.

For batted balls with launch_speed and launch_angle:
  1. Predict hit_distance from EV + LA using a physics-based model
  2. Compute residual = actual - predicted
  3. Fit: residual ~ f(pitch_type, spin_rate, zone, batter, spray_angle)
  4. Interpretation: positive residuals → favorable batted-ball spin (carry)

This is a PROXY for the missing batted-ball spin vector.

Requires: data/pitches_enriched.csv with hc_x, hc_y (for spray_angle),
          and optionally release_spin_rate.
Run pull_enriched.py first.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import r2_score, mean_absolute_error

REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"


def physics_distance(launch_speed, launch_angle, elevation_ft=0.0):
    """Estimate batted ball distance from exit velocity and launch angle.

    Simplified drag model calibrated to MLB averages:
      d ≈ c₁ · EV^1.6 · sin(2·LA_rad) · drag_factor
    where drag_factor accounts for the typical ~35% distance loss vs vacuum.

    Parameters match Statcast ranges: EV in mph, LA in degrees, distance in feet.
    """
    la_rad = np.radians(launch_angle)

    # Vacuum trajectory distance (ft) at sea level
    g = 32.17  # ft/s²
    v0 = launch_speed * 5280 / 3600  # mph → ft/s
    d_vacuum = (v0**2 * np.sin(2 * la_rad)) / g

    # Empirical drag factor: ~0.62-0.68 of vacuum distance for fly balls
    # Calibrated from Statcast 2023-2024 batted ball data
    drag_factor = 0.64

    # Altitude bonus: ~1.5% per 1000 ft elevation
    alt_bonus = 1.0 + elevation_ft * 0.000015

    d_pred = np.abs(d_vacuum) * drag_factor * alt_bonus

    # Clamp to realistic range
    d_pred = np.clip(d_pred, 0, 550)

    return d_pred


def compute_spray_angle(hc_x, hc_y):
    """Compute spray angle from hit coordinates.

    0° = dead center, positive = right field, negative = left field.
    """
    dx = hc_x - 125.42
    dy = 198.27 - hc_y
    return np.degrees(np.arctan2(dx, dy))


def main():
    enriched_path = DATA_DIR / "pitches_enriched.csv"

    # Fall back to base pitches if enriched not available
    if not enriched_path.exists():
        enriched_path = DATA_DIR / "pitches_2025.csv"
        print(f"[residual] enriched data not found, using {enriched_path}")
    else:
        print(f"[residual] using enriched data: {enriched_path}")

    if not enriched_path.exists():
        print("ERROR: no pitch data found", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print("DISTANCE RESIDUAL MODEL — Batted-Ball Spin Proxy")
    print("=" * 70)

    print("\n[1/4] Loading data...")
    pitches = pd.read_csv(enriched_path)
    print(f"  {len(pitches):,} pitches")

    # Filter to batted balls with valid EV, LA, and distance
    batted = pitches[
        pitches["launch_speed"].notna()
        & pitches["launch_angle"].notna()
        & pitches["hit_distance_sc"].notna()
        & (pitches["hit_distance_sc"] > 10)
        & (pitches["launch_speed"] > 20)
    ].copy()
    print(f"  Batted balls with EV+LA+distance: {len(batted):,}")

    # Filter to fly balls / line drives (where spin matters most)
    if "bb_type" in batted.columns:
        air_balls = batted[batted["bb_type"].isin(["fly_ball", "line_drive", "popup"])].copy()
        print(f"  Air balls (fly/line/popup): {len(air_balls):,}")
    else:
        air_balls = batted.copy()

    print("\n[2/4] Computing physics baseline and residuals...")
    air_balls["d_predicted"] = physics_distance(
        air_balls["launch_speed"].values,
        air_balls["launch_angle"].values,
    )
    air_balls["d_residual"] = air_balls["hit_distance_sc"] - air_balls["d_predicted"]

    r2_base = r2_score(air_balls["hit_distance_sc"], air_balls["d_predicted"])
    mae_base = mean_absolute_error(air_balls["hit_distance_sc"], air_balls["d_predicted"])
    print(f"  Physics model: R²={r2_base:.3f}, MAE={mae_base:.1f} ft")
    print(f"  Residual stats: mean={air_balls['d_residual'].mean():.1f} ft, "
          f"std={air_balls['d_residual'].std():.1f} ft")
    print(f"  Residual range: [{air_balls['d_residual'].min():.0f}, "
          f"{air_balls['d_residual'].max():.0f}] ft")

    # Compute spray angle if hc_x/hc_y available
    has_spray = "hc_x" in air_balls.columns and air_balls["hc_x"].notna().any()
    if has_spray:
        air_balls["spray_angle"] = compute_spray_angle(
            air_balls["hc_x"].values, air_balls["hc_y"].values
        )
        print(f"  Spray angle: {air_balls['spray_angle'].notna().sum():,} values")
    elif "spray_angle" in air_balls.columns:
        has_spray = True
    else:
        print("  Spray angle: not available (no hc_x/hc_y)")

    has_spin = ("release_spin_rate" in air_balls.columns
                and air_balls["release_spin_rate"].notna().any())
    has_pfx = ("pfx_x" in air_balls.columns and air_balls["pfx_x"].notna().any())

    print("\n[3/4] Fitting residual models...")
    # Build feature matrix for residual prediction
    feature_cols = []
    feature_names = []

    # Always-available features
    air_balls["la_squared"] = air_balls["launch_angle"] ** 2
    feature_cols += ["launch_angle", "la_squared", "launch_speed"]
    feature_names += ["LA", "LA²", "EV"]

    # Pitch type one-hot
    if "pitch_type" in air_balls.columns:
        pt_dummies = pd.get_dummies(air_balls["pitch_type"], prefix="pt")
        for c in pt_dummies.columns:
            air_balls[c] = pt_dummies[c]
            feature_cols.append(c)
            feature_names.append(c)

    # Zone one-hot
    if "zone" in air_balls.columns:
        zone_dummies = pd.get_dummies(air_balls["zone"].astype("Int64"), prefix="z")
        for c in zone_dummies.columns:
            air_balls[c] = zone_dummies[c]
            feature_cols.append(c)
            feature_names.append(c)

    # Spray angle (if available)
    if has_spray:
        air_balls["spray_abs"] = air_balls["spray_angle"].abs()
        feature_cols += ["spray_angle", "spray_abs"]
        feature_names += ["spray_angle", "|spray_angle|"]

    # Pitch spin/movement (if available)
    if has_spin:
        feature_cols.append("release_spin_rate")
        feature_names.append("spin_rate")
    if has_pfx:
        feature_cols += ["pfx_x", "pfx_z"]
        feature_names += ["pfx_x", "pfx_z"]

    # Drop rows with NaN in features
    valid = air_balls.dropna(subset=feature_cols + ["d_residual"]).copy()
    print(f"  Complete cases: {len(valid):,}")

    if len(valid) < 1000:
        print("ERROR: too few complete cases to fit reliably")
        sys.exit(1)

    X = valid[feature_cols].values.astype(float)
    y_resid = valid["d_residual"].values

    # Temporal split
    valid["game_date"] = pd.to_datetime(valid["game_date"])
    dates = valid["game_date"]
    unique_dates = sorted(dates.unique())
    cutoff = unique_dates[int(len(unique_dates) * 0.8)]
    train_mask = dates < cutoff
    X_train, y_train = X[train_mask.values], y_resid[train_mask.values]
    X_test, y_test = X[~train_mask.values], y_resid[~train_mask.values]

    print(f"  Train: {len(y_train):,}, Test: {len(y_test):,}")
    print(f"  Cutoff: {cutoff}")

    results = {}

    # Constant baseline (predict mean residual)
    y_pred_const = np.full(len(y_test), y_train.mean())
    results["constant"] = {
        "r2": float(r2_score(y_test, y_pred_const)),
        "mae": float(mean_absolute_error(y_test, y_pred_const)),
    }
    print(f"\n  Constant: R²={results['constant']['r2']:.4f}, "
          f"MAE={results['constant']['mae']:.1f} ft")

    # Ridge regression
    ridge = Ridge(alpha=1.0)
    ridge.fit(X_train, y_train)
    y_pred_ridge = ridge.predict(X_test)
    results["ridge"] = {
        "r2": float(r2_score(y_test, y_pred_ridge)),
        "mae": float(mean_absolute_error(y_test, y_pred_ridge)),
    }
    print(f"  Ridge:    R²={results['ridge']['r2']:.4f}, "
          f"MAE={results['ridge']['mae']:.1f} ft")

    # Top ridge coefficients
    coef_pairs = sorted(zip(feature_names, ridge.coef_),
                        key=lambda x: abs(x[1]), reverse=True)
    print(f"\n  Ridge top features (explaining residual = spin proxy):")
    for name, coef in coef_pairs[:10]:
        print(f"    {name:<25s}  {coef:+.3f}")

    # GBT for nonlinear effects
    gbt = GradientBoostingRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, random_state=42,
    )
    gbt.fit(X_train, y_train)
    y_pred_gbt = gbt.predict(X_test)
    results["gradient_boosting"] = {
        "r2": float(r2_score(y_test, y_pred_gbt)),
        "mae": float(mean_absolute_error(y_test, y_pred_gbt)),
    }
    print(f"\n  GBT:      R²={results['gradient_boosting']['r2']:.4f}, "
          f"MAE={results['gradient_boosting']['mae']:.1f} ft")

    # GBT feature importances
    imp = sorted(zip(feature_names, gbt.feature_importances_),
                 key=lambda x: x[1], reverse=True)
    print(f"\n  GBT feature importances:")
    for name, importance in imp[:10]:
        print(f"    {name:<25s}  {importance:.4f}")

    print("\n[4/4] Batter-level residual analysis...")
    valid["d_residual_pred"] = np.nan
    valid.loc[train_mask.values, "d_residual_pred"] = ridge.predict(X_train)
    valid.loc[~train_mask.values, "d_residual_pred"] = y_pred_ridge

    # The unexplained residual (actual - model_pred) is the spin proxy
    valid["spin_proxy"] = valid["d_residual"] - valid["d_residual_pred"]

    # Per-batter average spin proxy (on test set only)
    test_data = valid[~train_mask.values].copy()
    batter_spin = test_data.groupby("batter").agg(
        n_batted=("spin_proxy", "size"),
        mean_spin_proxy=("spin_proxy", "mean"),
        std_spin_proxy=("spin_proxy", "std"),
        mean_ev=("launch_speed", "mean"),
        mean_la=("launch_angle", "mean"),
    ).reset_index()
    batter_spin = batter_spin[batter_spin["n_batted"] >= 20].copy()
    batter_spin = batter_spin.sort_values("mean_spin_proxy", ascending=False)

    print(f"\n  Batters with ≥20 air balls (test set): {len(batter_spin)}")
    print(f"\n  Top 10 batters by spin proxy (favorable carry):")
    print(f"  {'Batter':>10s}  {'n':>4s}  {'spin_proxy':>11s}  {'EV':>6s}  {'LA':>6s}")
    for _, r in batter_spin.head(10).iterrows():
        print(f"  {int(r['batter']):>10d}  {int(r['n_batted']):>4d}  "
              f"{r['mean_spin_proxy']:>+11.1f} ft  "
              f"{r['mean_ev']:>6.1f}  {r['mean_la']:>6.1f}")

    print(f"\n  Bottom 10 batters by spin proxy (drag / poor carry):")
    for _, r in batter_spin.tail(10).iterrows():
        print(f"  {int(r['batter']):>10d}  {int(r['n_batted']):>4d}  "
              f"{r['mean_spin_proxy']:>+11.1f} ft  "
              f"{r['mean_ev']:>6.1f}  {r['mean_la']:>6.1f}")

    # Save results
    results["physics_baseline"] = {"r2": float(r2_base), "mae": float(mae_base)}
    results["n_air_balls"] = len(air_balls)
    results["n_complete"] = len(valid)
    results["features_used"] = feature_names
    results["has_spray"] = has_spray
    results["has_spin"] = has_spin
    results["has_pfx"] = has_pfx

    out_path = DATA_DIR / "distance_residual_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")

    print(f"\n{'='*70}")
    print("INTERPRETATION")
    print(f"{'='*70}")
    print(f"  Physics model (EV + LA → distance) explains R²={r2_base:.3f}")
    print(f"  Ridge residual model adds R²={results['ridge']['r2']:.4f}")
    print(f"  The residual captures batted-ball spin effects:")
    print(f"    positive = more carry than physics predicts (backspin)")
    print(f"    negative = less carry (topspin, drag)")
    print(f"  Per-batter spin proxy identifies consistent carry/drag tendencies.")


if __name__ == "__main__":
    main()
