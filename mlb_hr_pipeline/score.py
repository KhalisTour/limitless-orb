"""
Phase 4 — Score predictions against actual game outcomes.

For a given date (default: yesterday), load predictions_<date>.json, pull that
day's Statcast PAs, count HRs per batter per game, and emit per-prediction
calibration rows appended to data/calibration_log.csv. Also reports running
Brier, log-loss, AUC across the full log.

A "prediction" here is a per-game probability that a hitter records the outcome
at least once. For HR we use sim's p_at_least_one_hr (the natural unit for "did
Hitter X homer in Game Y?"); otherwise 1 - (1 - p_per_pa) ** exp_pa.

XBH and hits are scored alongside HR. They were not before, which is how
models_xbh.py and models_hit.py stayed byte-identical copies of the HR model for
as long as they did: the pipeline published their numbers on the site but never
compared a single one of them to a result, so there was no signal that anything
was wrong. Anything published gets scored.
"""

import json
import math
import re
import sys
import traceback
import unicodedata
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score


REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
LOG_PATH = DATA_DIR / "calibration_log.csv"


def _per_game(ppa, exp_pa):
    """P(at least one) over a game's expected plate appearances."""
    if ppa is None:
        return None
    ppa = min(max(float(ppa), 0.0), 1.0)
    return 1.0 - (1.0 - ppa) ** float(exp_pa or 4.2)


TARGETS = (("hr", "p_hr", "actual_hr"),
           ("xbh", "p_xbh", "actual_xbh"),
           ("hit", "p_hit", "actual_hit"))

MIN_SCORED_FOR_METRICS = 25


def metrics_for(frame: pd.DataFrame, pcol: str, ycol: str) -> dict | None:
    """Scoring metrics for one target over `frame`, against a constant-rate baseline.

    Returns None when the column is absent or there are too few scored rows for
    the numbers to mean anything.
    """
    if pcol not in frame.columns or ycol not in frame.columns:
        return None
    sub = frame[[pcol, ycol]].dropna()
    if len(sub) < MIN_SCORED_FOR_METRICS:
        return None
    y = sub[ycol].values.astype(int)
    p = sub[pcol].clip(1e-6, 1 - 1e-6).values.astype(float)
    base = np.full(len(y), y.mean())
    m = {
        "n": int(len(y)),
        "rate_actual": float(y.mean()),
        "rate_pred": float(p.mean()),
        "bias_pct": float((p.mean() / y.mean() - 1) * 100) if y.mean() else None,
        "brier": float(brier_score_loss(y, p)),
        "baseline_brier": float(brier_score_loss(y, base)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "baseline_log_loss": float(log_loss(y, base, labels=[0, 1])),
    }
    m["auc"] = float(roc_auc_score(y, p)) if len(set(y)) >= 2 else None
    m["beats_baseline"] = m["log_loss"] < m["baseline_log_loss"]
    return m


def split_by_generation(full: pd.DataFrame, generation) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(all rows, rows from `generation`). Empty current frame when none match."""
    if "model_generation" in full.columns and generation is not None:
        return full, full[full["model_generation"] == generation]
    return full, full.iloc[0:0]


def _pred_p_hr_in_game(per_hitter_entry, sim_block, hitter):
    if sim_block and isinstance(sim_block, dict) and "p_at_least_one_hr" in sim_block:
        v = sim_block["p_at_least_one_hr"].get(hitter)
        if v is not None:
            return float(v)
    return _per_game(per_hitter_entry.get("p_per_pa"), per_hitter_entry.get("exp_pa", 4.2))


def _pred_p_xbh_in_game(per_hitter_entry):
    blk = per_hitter_entry.get("xbh") or {}
    return _per_game(blk.get("per_pa"), per_hitter_entry.get("exp_pa", 4.2))


def _pred_p_hit_in_game(per_hitter_entry):
    blk = per_hitter_entry.get("hit") or {}
    return _per_game(blk.get("per_pa"), per_hitter_entry.get("exp_pa", 4.2))


def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[.\-']", " ", name)
    parts = name.lower().split()
    if len(parts) >= 2 and "," in name:
        comma_parts = name.split(",", 1)
        parts = comma_parts[1].strip().lower().split() + comma_parts[0].strip().lower().split()
    return " ".join(parts)


def _load_batter_id_map(date: str) -> dict:
    """Build batter MLBAM ID -> normalized name from the snapshot CSV."""
    snap_dir = REPO / "data" / "snapshots" / date
    bat_path = snap_dir / f"batters_{date}.csv"
    if not bat_path.exists():
        return {}
    df = pd.read_csv(bat_path)
    id_col = None
    for c in ["player_id", "batter_id", "mlbam_id", "id"]:
        if c in df.columns:
            id_col = c
            break
        for col in df.columns:
            if col.lower() == c.lower():
                id_col = col
                break
        if id_col:
            break
    name_col = None
    for c in ["player_name", "name", "full_name", "last_name, first_name"]:
        if c in df.columns:
            name_col = c
            break
    if not id_col or not name_col:
        return {}
    result = {}
    for _, row in df.iterrows():
        pid = row[id_col]
        if pd.notna(pid):
            raw = str(row[name_col])
            if "," in raw:
                parts = raw.split(",", 1)
                flipped = f"{parts[1].strip()} {parts[0].strip()}"
            else:
                flipped = raw
            result[int(pid)] = _normalize_name(flipped)
    return result


XBH_EVENTS = ("double", "triple", "home_run")
HIT_EVENTS = ("single", "double", "triple", "home_run")
COLS = ["game_pk", "batter_id", "hr_count", "xbh_count", "hit_count"]


def pull_actuals(date: str) -> pd.DataFrame:
    """Per-(game, batter_id) HR / XBH / hit counts from Statcast for `date`.
    NOTE: Statcast player_name is the PITCHER, not the batter.
    We use the numeric `batter` column (MLBAM ID) instead.
    """
    from pybaseball import statcast
    from pybaseball import cache
    cache.enable()
    df = statcast(start_dt=date, end_dt=date)
    if df.empty or "events" not in df.columns:
        return pd.DataFrame(columns=COLS)
    df = df[df["events"].isin(HIT_EVENTS)]
    if df.empty:
        return pd.DataFrame(columns=COLS)
    df = df.assign(
        _hr=(df["events"] == "home_run").astype(int),
        _xbh=df["events"].isin(XBH_EVENTS).astype(int),
        _hit=1,
    )
    agg = (df.groupby(["game_pk", "batter"])[["_hr", "_xbh", "_hit"]]
             .sum().reset_index()
             .rename(columns={"batter": "batter_id", "_hr": "hr_count",
                              "_xbh": "xbh_count", "_hit": "hit_count"}))
    return agg[COLS]


def score_for_date(date: str):
    preds_path = DATA_DIR / f"predictions_{date}.json"
    if not preds_path.exists():
        raise RuntimeError(f"No predictions file at {preds_path}")
    preds = json.loads(preds_path.read_text())

    actuals = pull_actuals(date)
    id_map = _load_batter_id_map(date)

    # Resolve batter IDs to names and build lookups
    if actuals.empty:
        print(f"[score] WARNING: Statcast returned NO home runs for {date}")
    else:
        print(f"[score] Statcast HRs: {len(actuals)} batter-game rows")
        for _, r in actuals.iterrows():
            bid = int(r["batter_id"])
            name = id_map.get(bid, f"?id={bid}")
            print(f"  game_pk={r['game_pk']}  batter_id={bid}  -> {name}  (count={r['hr_count']})")

    # Names only — do NOT rebind TARGETS here: the module-level TARGETS is a
    # tuple of (name, pred_col, actual_col) triples and the metrics loop below
    # unpacks all three from it.
    target_names = tuple(t for t, _, _ in TARGETS)
    actual_by_game = {t: {} for t in target_names}
    actual_by_name = {t: {} for t in target_names}
    for r in actuals.itertuples():
        bid = int(r.batter_id)
        name = id_map.get(bid)
        if name is None:
            continue
        for t, col in (("hr", r.hr_count), ("xbh", r.xbh_count), ("hit", r.hit_count)):
            actual_by_game[t][(int(r.game_pk), name)] = int(col)
            actual_by_name[t][name] = actual_by_name[t].get(name, 0) + int(col)

    pred_game_ids = set()
    rows = []
    for g in preds.get("games", []):
        gid = g["game_id"]
        pred_game_ids.add(int(gid))
        for side in ("away", "home"):
            sb = g["sides"].get(side, {})
            if not isinstance(sb, dict) or sb.get("error"):
                continue
            sim_block = sb.get("sim") or {}
            for hitter, entry in sb.get("per_hitter", {}).items():
                if "error" in entry:
                    continue
                p_hr = _pred_p_hr_in_game(entry, sim_block, hitter)
                if p_hr is None:
                    continue
                hn = _normalize_name(hitter)

                def _actual(t):
                    v = actual_by_game[t].get((int(gid), hn))
                    return actual_by_name[t].get(hn, 0) if v is None else v

                a_hr, a_xbh, a_hit = _actual("hr"), _actual("xbh"), _actual("hit")
                p_xbh = _pred_p_xbh_in_game(entry)
                p_hit = _pred_p_hit_in_game(entry)
                dq = sb.get("data_quality") or {}
                rows.append(dict(
                    game_date=date, game_id=gid, side=side,
                    hitter=hitter, opp_pitcher=sb.get("pitcher"),
                    p_hr=p_hr, p_per_pa=entry.get("p_per_pa"),
                    p_xbh=p_xbh, p_xbh_per_pa=(entry.get("xbh") or {}).get("per_pa"),
                    p_hit=p_hit, p_hit_per_pa=(entry.get("hit") or {}).get("per_pa"),
                    exp_pa=entry.get("exp_pa"),
                    actual_hr=int(a_hr >= 1), actual_hr_count=a_hr,
                    actual_xbh=int(a_xbh >= 1), actual_xbh_count=a_xbh,
                    actual_hit=int(a_hit >= 1), actual_hit_count=a_hit,
                    # Recorded so calibrate.py can invert the map that was in
                    # effect when the row was written, and so a calibration fit
                    # from a different model generation is not applied silently.
                    lineup_confirmed=(dq.get("lineup") or {}).get("confirmed"),
                    pitcher_level=(dq.get("pitcher") or {}).get("level"),
                    model_generation=preds.get("model_generation"),
                    residual=int(a_hr >= 1) - p_hr,
                ))

    # Debug: check for game_id overlap
    actual_game_pks = set(int(r.game_pk) for r in actuals.itertuples()) if not actuals.empty else set()
    overlap = pred_game_ids & actual_game_pks
    print(f"[score] pred game_ids: {sorted(pred_game_ids)}")
    print(f"[score] actual game_pks: {sorted(actual_game_pks)}")
    print(f"[score] overlap: {len(overlap)} games  matched_hrs: {sum(1 for r in rows if r['actual_hr'] > 0)}")

    if not rows:
        print(f"[score] no scoreable predictions for {date}")
        return None

    new_df = pd.DataFrame(rows)
    if LOG_PATH.exists():
        old = pd.read_csv(LOG_PATH)
        # avoid duplicates on rerun
        key_cols = ["game_date", "game_id", "side", "hitter"]
        old_keys = set(map(tuple, old[key_cols].astype(str).values.tolist()))
        new_df_filt = new_df[~new_df[key_cols].astype(str).apply(tuple, axis=1).isin(old_keys)]
        full = pd.concat([old, new_df_filt], ignore_index=True)
    else:
        full = new_df
    full.to_csv(LOG_PATH, index=False)
    print(f"[score] appended {len(new_df)} rows to {LOG_PATH}  (total={len(full)})")

    # Running metrics, per target, against what a constant league-rate forecast
    # would score. A model that does not beat that number is not adding
    # information.
    #
    # Split by model generation. Pooling every row ever logged makes the running
    # numbers a verdict on whichever model dominates the log, not on the one
    # currently running — right after a model change that is entirely the OLD
    # model, and it takes weeks of slates before the new one shows through. The
    # "current" block is the only one that says anything about today's model;
    # "all_time" is kept because that is what the log has always reported.
    import models as _models
    generation = getattr(_models, "MODEL_GENERATION", None)

    full, current = split_by_generation(full, generation)

    metrics = {"model_generation": generation,
               "n_current_generation": int(len(current)),
               "all_time": {}, "current": {}}
    for t, pcol, ycol in TARGETS:
        m_all = metrics_for(full, pcol, ycol)
        if m_all:
            metrics["all_time"][t] = m_all
        m_cur = metrics_for(current, pcol, ycol)
        if m_cur:
            metrics["current"][t] = m_cur

    # Warn on the running model only. Warning off all-time numbers after a model
    # change means warning about a model that is no longer in the pipeline.
    if metrics["current"]:
        for t, m in metrics["current"].items():
            if not m["beats_baseline"]:
                print(f"[score] WARNING: {t} predictions from the current model "
                      f"(generation {generation}, n={m['n']}) score worse than a "
                      f"constant league-rate guess (log-loss {m['log_loss']:.5f} "
                      f"vs {m['baseline_log_loss']:.5f}). Re-run calibrate.py.")
    else:
        older = {t: m["n"] for t, m in metrics["all_time"].items()}
        print(f"[score] no scored rows yet from model generation {generation}; "
              f"the running metrics below describe earlier generations "
              f"({older}) and say nothing about the model now in the pipeline. "
              f"They will turn over as new slates are scored.")

    # Keep the flat HR keys the older log consumers read. Prefer the current
    # generation once it has rows, so the API's accuracy page tracks the model
    # that is actually running.
    hr = metrics["current"].get("hr") or metrics["all_time"].get("hr")
    if hr:
        metrics.update({k: hr[k] for k in
                        ("n", "rate_actual", "rate_pred", "brier", "log_loss", "auc")})
    print(f"[score] running metrics: {json.dumps(metrics, indent=2)}")
    (DATA_DIR / "calibration_metrics.json").write_text(json.dumps(metrics, indent=2))
    return metrics


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            d = sys.argv[1]
        else:
            d = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        score_for_date(d)
    except Exception:
        traceback.print_exc()
        print("\nRerun: python score.py [YYYY-MM-DD]", file=sys.stderr)
        sys.exit(1)
