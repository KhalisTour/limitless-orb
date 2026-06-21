"""
Phase 1 — Fit Model 3 logistic regression on the per-PA backtest table.

Steps:
  1. Load backtest.csv (per-PA, with hr 0/1 label)
  2. Pull season Savant CSVs for batters + pitchers via fetch.get_batter_season /
     get_pitcher_season for the same season as the backtest
  3. Join season features onto each PA by player_id
  4. Fit a logistic regression on z-scored features
  5. Write fitted_coefficients.json (intercept + per-feature coefs)
  6. Report calibration: 10 bins of predicted prob vs actual HR rate

Why logistic: binary outcome (HR/no-HR), interpretable coefficients in log-odds,
and we can compare fitted weights to the priors hard-coded in models.M3.
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
MODELS_DIR = REPO / "models_out"  # avoid name clash with models.py
MODELS_DIR.mkdir(exist_ok=True)


# Map Savant CSV columns -> the canonical feature keys we'll fit on.
# Savant column names occasionally drift; we resolve to the first present column.
BATTER_COL_MAP = {
    "barrel":  ["barrel_batted_rate", "brl_percent", "brl_pa"],
    "xslg":    ["xslg"],
    "hardhit": ["hard_hit_percent"],
    "la":      ["launch_angle_avg", "avg_launch_angle"],
    "whiff":   ["whiff_percent"],
    "k":       ["k_percent"],
    "xwoba":   ["xwoba"],
}

PITCHER_COL_MAP = {
    "p_barrel":  ["barrel_batted_rate", "brl_percent"],
    "p_hardhit": ["hard_hit_percent"],
    "p_xslg":    ["xslg"],
    "p_xwoba":   ["xwoba"],
    "p_k":       ["k_percent"],
    "p_whiff":   ["whiff_percent"],
}


def resolve(df: pd.DataFrame, spec: dict, label: str) -> dict:
    """Return {canonical_key: actual_column} after probing candidates."""
    resolved = {}
    missing = []
    for key, cands in spec.items():
        for c in cands:
            if c in df.columns:
                resolved[key] = c
                break
        else:
            missing.append((key, cands))
    print(f"[fit] {label} column mapping resolved {len(resolved)}/{len(spec)}")
    for k, col in resolved.items():
        print(f"       {k:<10} -> {col}")
    for k, cands in missing:
        print(f"       MISSING {k} (tried {cands})")
    return resolved


def find_id_col(df: pd.DataFrame, kind: str) -> str:
    for c in ["player_id", f"{kind}_id", "mlbam_id", "id"]:
        if c in df.columns:
            return c
        # case-insensitive
        for col in df.columns:
            if col.lower() == c.lower():
                return col
    raise RuntimeError(f"No id column on {kind} CSV. Columns: {list(df.columns)[:20]}")


def main():
    backtest_csv = DATA_DIR / "backtest.csv"
    if not backtest_csv.exists():
        raise RuntimeError(f"Missing {backtest_csv}. Run backtest.py first.")
    pa = pd.read_csv(backtest_csv)
    print(f"[fit] backtest rows: {len(pa):,}  HRs: {int(pa['hr'].sum()):,}")

    # Infer the season from the PA dates
    pa["game_date"] = pd.to_datetime(pa["game_date"])
    season = int(pa["game_date"].dt.year.mode().iloc[0])
    print(f"[fit] season: {season}")

    from fetch import get_batter_season, get_pitcher_season
    print("[fit] pulling season batter table")
    bat = get_batter_season(year=season)
    print(f"       batter rows={len(bat)}  cols={len(bat.columns)}")
    print("[fit] pulling season pitcher table")
    pit = get_pitcher_season(year=season)
    print(f"       pitcher rows={len(pit)}  cols={len(pit.columns)}")

    bat_map = resolve(bat, BATTER_COL_MAP, "batter")
    pit_map = resolve(pit, PITCHER_COL_MAP, "pitcher")

    bat_id = find_id_col(bat, "batter")
    pit_id = find_id_col(pit, "pitcher")

    bat_feats = bat[[bat_id] + list(bat_map.values())].rename(
        columns={bat_id: "batter", **{v: k for k, v in bat_map.items()}}
    )
    pit_feats = pit[[pit_id] + list(pit_map.values())].rename(
        columns={pit_id: "pitcher", **{v: k for k, v in pit_map.items()}}
    )

    # Join. Statcast batter/pitcher columns are mlbam IDs (ints).
    df = pa.merge(bat_feats, on="batter", how="left").merge(pit_feats, on="pitcher", how="left")
    bat_join_pct = df[list(bat_map.keys())[0]].notna().mean() if bat_map else 0
    pit_join_pct = df[list(pit_map.keys())[0]].notna().mean() if pit_map else 0
    print(f"[fit] join success: batter={bat_join_pct:.1%}  pitcher={pit_join_pct:.1%}")

    # Prior-year pitcher cascade: fill unmatched pitcher PAs from the previous season board
    if pit_map:
        first_pit_col = list(pit_map.keys())[0]
        pit_miss = df[first_pit_col].isna()
        if pit_miss.any():
            prev_season = season - 1
            prev_pit_path = DATA_DIR / f"pitchers_{prev_season}.csv"
            if prev_pit_path.exists():
                print(f"[fit] trying {prev_season} pitcher cascade for {pit_miss.sum():,} unmatched PAs")
                pit_prev = pd.read_csv(prev_pit_path)
                pit_map_prev = resolve(pit_prev, PITCHER_COL_MAP, f"pitcher_{prev_season}")
                if pit_map_prev:
                    prev_pit_id = find_id_col(pit_prev, "pitcher")
                    pit_feats_prev = pit_prev[[prev_pit_id] + list(pit_map_prev.values())].rename(
                        columns={prev_pit_id: "pitcher", **{v: k for k, v in pit_map_prev.items()}}
                    ).set_index("pitcher")
                    for col in pit_map.keys():
                        if col in pit_feats_prev.columns:
                            df.loc[pit_miss, col] = df.loc[pit_miss, "pitcher"].map(pit_feats_prev[col])
                    pit_join_pct2 = df[first_pit_col].notna().mean()
                    print(f"[fit] after {prev_season} cascade: pitcher join={pit_join_pct2:.1%}")
            else:
                print(f"[fit] {prev_pit_path.name} not found — run ingest_live.py once to cache it")

    feature_cols = list(bat_map.keys()) + list(pit_map.keys())
    complete = df.dropna(subset=feature_cols).copy()
    print(f"[fit] complete-case PAs: {len(complete):,} / {len(df):,} "
          f"({len(complete)/len(df):.1%}); HR rate in fit set: {complete['hr'].mean():.4f}")
    if len(complete) < 5000:
        raise RuntimeError(f"Too few complete-case rows ({len(complete)}) to fit reliably.")

    X = complete[feature_cols].values.astype(float)
    y = complete["hr"].values.astype(int)

    # Z-score to keep coefficients comparable to the M3 priors
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    Xz = (X - mu) / sd

    clf = LogisticRegression(max_iter=2000, C=1.0)
    clf.fit(Xz, y)
    p = clf.predict_proba(Xz)[:, 1]

    metrics = {
        "n": int(len(y)),
        "hr_rate": float(y.mean()),
        "log_loss": float(log_loss(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "auc": float(roc_auc_score(y, p)),
    }
    print(f"[fit] in-sample metrics: {metrics}")

    # 10-bin calibration
    bins = np.linspace(0, 1, 11)
    binned = pd.DataFrame({"y": y, "p": p})
    binned["bin"] = pd.cut(binned["p"], bins, include_lowest=True)
    cal = binned.groupby("bin", observed=True).agg(
        n=("y", "size"), pred=("p", "mean"), actual=("y", "mean")
    ).reset_index()
    cal["bin"] = cal["bin"].astype(str)
    print("[fit] calibration (predicted vs actual):")
    for _, r in cal.iterrows():
        print(f"   {r['bin']:<18} n={int(r['n']):>6}  pred={r['pred']:.4f}  actual={r['actual']:.4f}")

    # Try to write a calibration plot if matplotlib is available
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.plot([0, 1], [0, 1], "k--", lw=1, alpha=0.5)
        ax.scatter(cal["pred"], cal["actual"], s=cal["n"] / max(1, cal["n"].max()) * 200)
        ax.set_xlabel("Predicted HR probability")
        ax.set_ylabel("Actual HR rate")
        ax.set_title(f"Calibration ({season}, n={metrics['n']:,})")
        plot_path = MODELS_DIR / "calibration_plot.png"
        fig.savefig(plot_path, dpi=120, bbox_inches="tight")
        print(f"[fit] wrote {plot_path}")
    except Exception as e:
        print(f"[fit] plot skipped: {e}")

    out = {
        "season": season,
        "fit_at": dt.datetime.utcnow().isoformat() + "Z",
        "features": feature_cols,
        "feature_means": dict(zip(feature_cols, mu.tolist())),
        "feature_stds": dict(zip(feature_cols, sd.tolist())),
        "intercept": float(clf.intercept_[0]),
        "coefficients": dict(zip(feature_cols, clf.coef_[0].tolist())),
        "metrics": metrics,
        "calibration": cal.to_dict(orient="records"),
        "batter_column_map": {k: v for k, v in bat_map.items()},
        "pitcher_column_map": {k: v for k, v in pit_map.items()},
    }
    out_path = MODELS_DIR / "fitted_coefficients.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"[fit] wrote {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("\nRerun: python fit_model.py", file=sys.stderr)
        sys.exit(1)
