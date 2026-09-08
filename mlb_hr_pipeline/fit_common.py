"""
fit_common.py — shared fitting machinery for the HR / XBH / hit Model-3 fits.

fit_model.py, fit_model_xbh.py and fit_model_hit.py were three near-identical
copies of the same 200 lines. That is how models_xbh.py and models_hit.py ended
up as copies of models.py too: when the only way to add a target is to duplicate
a file, the duplicate is what gets shipped. Everything target-agnostic lives
here now and each fit script is a spec plus a call.

What this does that the copies did not:

  * Holds out the last quarter of the date range and reports metrics on it.
    The old scripts reported in-sample numbers only, which is why an HR model
    with a 0.58 holdout AUC was shipped advertising 0.64.
  * Tunes the L2 strength on that holdout instead of leaving C=1.0.
  * Fits exactly the features the prediction path reads, so predict_today can
    install the whole coefficient vector. Fitting a wider set and copying part
    of it out is not safe: the shipped HR fit put xwoba at -0.198 to offset
    xslg at +0.278, and dropping xwoba on the way to models.M3 left the power
    term at more than double its fitted value.
  * Checks each fitted coefficient against its univariate direction and prints
    a warning when they disagree, which is the signature of a collinear feature
    set producing coefficients that are only valid together.
  * Falls back to cached snapshot boards when Savant is unreachable, so a fit
    can be reproduced offline.
"""

import json
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

# Fraction of the date range held out (chronologically) for model selection.
HOLDOUT_FRAC = 0.25
# L2 grid searched on the holdout.
C_GRID = (1.0, 0.3, 0.1, 0.03, 0.01)

XBH_EVENTS = ("double", "triple", "home_run")
HIT_EVENTS = ("single", "double", "triple", "home_run")

# Canonical feature key -> candidate Savant column names. Keys match the hitter
# dict keys ingest_live produces, so a fitted coefficient lands on the same
# field the model reads at predict time.
BATTER_COL_MAP = {
    "barrel":  ["barrel_batted_rate", "brl_percent", "brl_pa"],
    "xslg":    ["xslg"],
    "hardhit": ["hard_hit_percent"],
    "la":      ["launch_angle_avg", "avg_launch_angle"],
    "ev":      ["exit_velocity_avg", "avg_best_speed"],
    "whiff":   ["whiff_percent"],
    "k":       ["k_percent"],
    "xwoba":   ["xwoba"],
    "xba":     ["xba"],
}

PITCHER_COL_MAP = {
    "p_barrel":  ["barrel_batted_rate", "brl_percent"],
    "p_hardhit": ["hard_hit_percent"],
    "p_xslg":    ["xslg"],
    "p_xwoba":   ["xwoba"],
    "p_k":       ["k_percent"],
    "p_whiff":   ["whiff_percent"],
}


def label_for(events: pd.Series, target: str) -> pd.Series:
    if target == "hr":
        return (events == "home_run").astype(int)
    if target == "xbh":
        return events.isin(XBH_EVENTS).astype(int)
    if target == "hit":
        return events.isin(HIT_EVENTS).astype(int)
    raise ValueError(f"unknown target {target!r}")


def resolve(df: pd.DataFrame, spec: dict, label: str) -> dict:
    """Return {canonical_key: actual_column} after probing candidates."""
    resolved, missing = {}, []
    for key, cands in spec.items():
        for c in cands:
            if c in df.columns and df[c].notna().any():
                resolved[key] = c
                break
        else:
            missing.append((key, cands))
    print(f"[fit] {label} column mapping resolved {len(resolved)}/{len(spec)}")
    for k, cands in missing:
        print(f"       MISSING {k} (tried {cands})")
    return resolved


def find_id_col(df: pd.DataFrame, kind: str) -> str:
    for c in ["player_id", f"{kind}_id", "mlbam_id", "id"]:
        for col in df.columns:
            if col.lower() == c.lower():
                return col
    raise RuntimeError(f"No id column on {kind} CSV. Columns: {list(df.columns)[:20]}")


def _latest_snapshot_board(kind: str, year: int):
    """Most recent cached snapshot board, used when Savant is unreachable."""
    snaps = sorted((DATA_DIR / "snapshots").glob("*/"), reverse=True) \
        if (DATA_DIR / "snapshots").exists() else []
    for snap in snaps:
        for p in snap.glob(f"{kind}_*.csv"):
            if p.name.endswith("_allpa.csv"):
                continue
            return p
    p = DATA_DIR / f"{kind}_{year}.csv"
    return p if p.exists() else None


def load_board(kind: str, year: int) -> pd.DataFrame:
    """Season board for `kind` in {'batters','pitchers'}; cached copy on failure."""
    import fetch
    getter = fetch.get_batter_season if kind == "batters" else fetch.get_pitcher_season
    try:
        df = getter(year=year)
        print(f"[fit] pulled {kind} board: rows={len(df)} cols={len(df.columns)}")
        return df
    except Exception as e:
        cached = _latest_snapshot_board(kind, year)
        if cached is None:
            raise RuntimeError(
                f"could not fetch {kind} board for {year} ({e}) and no cached "
                f"board exists under {DATA_DIR}") from e
        print(f"[fit] {kind} fetch failed ({type(e).__name__}); "
              f"falling back to cached {cached.relative_to(DATA_DIR)}")
        return pd.read_csv(cached)


def _univariate_signs(X: np.ndarray, y: np.ndarray, feats) -> dict:
    """Sign of each feature's one-at-a-time association with the label."""
    out = {}
    for i, f in enumerate(feats):
        m = LogisticRegression(max_iter=500).fit(X[:, [i]], y)
        out[f] = float(m.coef_[0][0])
    return out


def fit_target(target: str, features, out_name: str, tag: str = "fit"):
    """Fit one target and write its coefficients JSON. Returns the payload."""
    backtest_csv = DATA_DIR / "backtest.csv"
    if not backtest_csv.exists():
        raise RuntimeError(f"Missing {backtest_csv}. Run backtest.py first.")
    pa = pd.read_csv(backtest_csv)
    pa["y"] = label_for(pa["events"], target)
    pa["game_date"] = pd.to_datetime(pa["game_date"])
    season = int(pa["game_date"].dt.year.mode().iloc[0])
    print(f"[{tag}] {len(pa):,} PAs, season {season}, "
          f"{int(pa['y'].sum()):,} positives ({pa['y'].mean():.4f})")

    bat = load_board("batters", season)
    pit = load_board("pitchers", season)

    want_bat = {k: v for k, v in BATTER_COL_MAP.items() if k in features}
    want_pit = {k: v for k, v in PITCHER_COL_MAP.items() if k in features}
    bat_map = resolve(bat, want_bat, "batter")
    pit_map = resolve(pit, want_pit, "pitcher")
    unresolved = [f for f in features if f not in bat_map and f not in pit_map]
    if unresolved:
        raise RuntimeError(
            f"[{tag}] requested features not present on the boards: {unresolved}. "
            f"Fitting a different feature set than the model reads is the bug "
            f"this check exists to prevent — fix the board or the spec.")

    bat_id, pit_id = find_id_col(bat, "batter"), find_id_col(pit, "pitcher")
    bat_feats = bat[[bat_id] + list(bat_map.values())].rename(
        columns={bat_id: "batter", **{v: k for k, v in bat_map.items()}})
    df = pa.merge(bat_feats, on="batter", how="left")
    if pit_map:
        pit_feats = pit[[pit_id] + list(pit_map.values())].rename(
            columns={pit_id: "pitcher", **{v: k for k, v in pit_map.items()}})
        df = df.merge(pit_feats, on="pitcher", how="left")

    feats = list(features)
    complete = df.dropna(subset=feats).sort_values("game_date").copy()
    print(f"[{tag}] complete-case PAs: {len(complete):,} / {len(df):,} "
          f"({len(complete)/max(1,len(df)):.1%}); rate {complete['y'].mean():.4f}")
    if len(complete) < 5000:
        raise RuntimeError(f"Too few complete-case rows ({len(complete)}) to fit reliably.")

    cut = complete["game_date"].quantile(1.0 - HOLDOUT_FRAC)
    tr, te = complete[complete.game_date <= cut], complete[complete.game_date > cut]
    print(f"[{tag}] train {len(tr):,} (through {cut.date()})  holdout {len(te):,}")

    # Standardize on the TRAIN window only, so the holdout is untouched by the
    # scaling; the published moments are refit on everything at the end.
    mu, sd = tr[feats].mean().values, tr[feats].std().values
    sd = np.where(sd == 0, 1.0, sd)
    Xtr, ytr = (tr[feats].values - mu) / sd, tr["y"].values
    Xte, yte = (te[feats].values - mu) / sd, te["y"].values

    best = None
    print(f"[{tag}] tuning L2 on the holdout:")
    for C in C_GRID:
        m = LogisticRegression(max_iter=3000, C=C).fit(Xtr, ytr)
        p = m.predict_proba(Xte)[:, 1]
        auc, ll = roc_auc_score(yte, p), log_loss(yte, p)
        print(f"        C={C:<6} holdout auc={auc:.4f} logloss={ll:.5f}")
        if best is None or ll < best[0]:
            best = (ll, auc, C)
    holdout_ll, holdout_auc, C = best
    print(f"[{tag}] selected C={C} (holdout auc={holdout_auc:.4f} logloss={holdout_ll:.5f})")

    m_hold = LogisticRegression(max_iter=3000, C=C).fit(Xtr, ytr)
    p_te = m_hold.predict_proba(Xte)[:, 1]
    base_te = np.full(len(yte), ytr.mean())

    # Final coefficients use every row, standardized on the full window.
    MU, SD = complete[feats].mean().values, complete[feats].std().values
    SD = np.where(SD == 0, 1.0, SD)
    Xall, yall = (complete[feats].values - MU) / SD, complete["y"].values
    clf = LogisticRegression(max_iter=3000, C=C).fit(Xall, yall)
    p_in = clf.predict_proba(Xall)[:, 1]

    uni = _univariate_signs(Xall, yall, feats)
    flipped = [f for f, u in uni.items()
               if u != 0 and np.sign(u) != np.sign(clf.coef_[0][feats.index(f)])]
    if flipped:
        print(f"[{tag}] WARNING: {flipped} have joint coefficients pointing "
              f"against their univariate direction — collinear feature set. "
              f"These coefficients are only valid together; do not copy a subset.")

    metrics = {
        "n": int(len(yall)),
        "rate": float(yall.mean()),
        "in_sample": {"log_loss": float(log_loss(yall, p_in)),
                      "brier": float(brier_score_loss(yall, p_in)),
                      "auc": float(roc_auc_score(yall, p_in))},
        "holdout": {"n": int(len(yte)),
                    "cutoff": str(cut.date()),
                    "log_loss": float(log_loss(yte, p_te)),
                    "brier": float(brier_score_loss(yte, p_te)),
                    "auc": float(roc_auc_score(yte, p_te)),
                    "baseline_log_loss": float(log_loss(yte, base_te)),
                    "baseline_brier": float(brier_score_loss(yte, base_te))},
    }
    h = metrics["holdout"]
    print(f"[{tag}] HOLDOUT  logloss {h['log_loss']:.5f} (baseline {h['baseline_log_loss']:.5f})"
          f"  brier {h['brier']:.5f} (baseline {h['baseline_brier']:.5f})  auc {h['auc']:.4f}")
    if h["log_loss"] >= h["baseline_log_loss"]:
        print(f"[{tag}] WARNING: holdout log-loss is no better than predicting the "
              f"league rate for everyone. The fit carries no usable signal.")

    binned = pd.DataFrame({"y": yte, "p": p_te})
    binned["bin"] = pd.qcut(binned["p"], 10, labels=False, duplicates="drop")
    cal = binned.groupby("bin", observed=True).agg(
        n=("y", "size"), pred=("p", "mean"), actual=("y", "mean")).reset_index()
    print(f"[{tag}] holdout decile calibration:")
    for _, r in cal.iterrows():
        print(f"        d{int(r['bin'])} n={int(r['n']):>6}  pred={r['pred']:.4f}  actual={r['actual']:.4f}")

    out = {
        "target": target,
        "season": season,
        "fit_at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "features": feats,
        "C": C,
        "feature_means": dict(zip(feats, MU.tolist())),
        "feature_stds": dict(zip(feats, SD.tolist())),
        "intercept": float(clf.intercept_[0]),
        "coefficients": dict(zip(feats, clf.coef_[0].tolist())),
        "univariate_coefficients": uni,
        "sign_conflicts": flipped,
        "metrics": metrics,
        "calibration_holdout": cal.to_dict(orient="records"),
        "batter_column_map": bat_map,
        "pitcher_column_map": pit_map,
    }
    out_path = MODELS_DIR / out_name
    out_path.write_text(json.dumps(out, indent=2))
    print(f"[{tag}] wrote {out_path}")
    return out
