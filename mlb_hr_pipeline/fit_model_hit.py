"""
fit_model_hit.py — Fit Model 3 logistic regression for hits (singles + doubles + triples + HRs).

Stub: mirrors fit_model.py structure but labels PAs as hit instead of HR.
Output goes to models_out/fitted_coefficients_hit.json.
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
    print(f"[fit_hit] {label} column mapping resolved {len(resolved)}/{len(spec)}")
    for k, col in resolved.items():
        print(f"          {k:<10} -> {col}")
    for k, cands in missing:
        print(f"          MISSING {k} (tried {cands})")
    return resolved


def find_id_col(df: pd.DataFrame, kind: str) -> str:
    for c in ["player_id", f"{kind}_id", "mlbam_id", "id"]:
        if c in df.columns:
            return c
        for col in df.columns:
            if col.lower() == c.lower():
                return col
    raise RuntimeError(f"No id column on {kind} CSV. Columns: {list(df.columns)[:20]}")


def main():
    backtest_csv = DATA_DIR / "backtest.csv"
    if not backtest_csv.exists():
        raise RuntimeError(f"Missing {backtest_csv}. Run backtest.py first.")
    pa = pd.read_csv(backtest_csv)

    # Build hit label from events column
    pa["hit"] = pa["events"].isin(["single", "double", "triple", "home_run"]).astype(int)
    print(f"[fit_hit] backtest rows: {len(pa):,}  Hits: {int(pa['hit'].sum()):,}")

    pa["game_date"] = pd.to_datetime(pa["game_date"])
    season = int(pa["game_date"].dt.year.mode().iloc[0])
    print(f"[fit_hit] season: {season}")

    from fetch import get_batter_season, get_pitcher_season
    print("[fit_hit] pulling season batter table")
    bat = get_batter_season(year=season)
    print(f"          batter rows={len(bat)}  cols={len(bat.columns)}")
    print("[fit_hit] pulling season pitcher table")
    pit = get_pitcher_season(year=season)
    print(f"          pitcher rows={len(pit)}  cols={len(pit.columns)}")

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

    df = pa.merge(bat_feats, on="batter", how="left").merge(pit_feats, on="pitcher", how="left")
    bat_join_pct = df[list(bat_map.keys())[0]].notna().mean() if bat_map else 0
    pit_join_pct = df[list(pit_map.keys())[0]].notna().mean() if pit_map else 0
    print(f"[fit_hit] join success: batter={bat_join_pct:.1%}  pitcher={pit_join_pct:.1%}")

    feature_cols = list(bat_map.keys()) + list(pit_map.keys())
    complete = df.dropna(subset=feature_cols).copy()
    print(f"[fit_hit] complete-case PAs: {len(complete):,} / {len(df):,} "
          f"({len(complete)/len(df):.1%}); hit rate in fit set: {complete['hit'].mean():.4f}")
    if len(complete) < 5000:
        raise RuntimeError(f"Too few complete-case rows ({len(complete)}) to fit reliably.")

    X = complete[feature_cols].values.astype(float)
    y = complete["hit"].values.astype(int)

    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    Xz = (X - mu) / sd

    clf = LogisticRegression(max_iter=2000, C=1.0)
    clf.fit(Xz, y)
    p = clf.predict_proba(Xz)[:, 1]

    metrics = {
        "n": int(len(y)),
        "hit_rate": float(y.mean()),
        "log_loss": float(log_loss(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "auc": float(roc_auc_score(y, p)),
    }
    print(f"[fit_hit] in-sample metrics: {metrics}")

    bins = np.linspace(0, 1, 11)
    binned = pd.DataFrame({"y": y, "p": p})
    binned["bin"] = pd.cut(binned["p"], bins, include_lowest=True)
    cal = binned.groupby("bin", observed=True).agg(
        n=("y", "size"), pred=("p", "mean"), actual=("y", "mean")
    ).reset_index()
    cal["bin"] = cal["bin"].astype(str)
    print("[fit_hit] calibration (predicted vs actual):")
    for _, r in cal.iterrows():
        print(f"   {r['bin']:<18} n={int(r['n']):>6}  pred={r['pred']:.4f}  actual={r['actual']:.4f}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.plot([0, 1], [0, 1], "k--", lw=1, alpha=0.5)
        ax.scatter(cal["pred"], cal["actual"], s=cal["n"] / max(1, cal["n"].max()) * 200)
        ax.set_xlabel("Predicted hit probability")
        ax.set_ylabel("Actual hit rate")
        ax.set_title(f"Hit Calibration ({season}, n={metrics['n']:,})")
        plot_path = MODELS_DIR / "calibration_plot_hit.png"
        fig.savefig(plot_path, dpi=120, bbox_inches="tight")
        print(f"[fit_hit] wrote {plot_path}")
    except Exception as e:
        print(f"[fit_hit] plot skipped: {e}")

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
    out_path = MODELS_DIR / "fitted_coefficients_hit.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"[fit_hit] wrote {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("\nRerun: python fit_model_hit.py", file=sys.stderr)
        sys.exit(1)
