"""In-memory cache of all pipeline output files.

Read-only. Scans DATA_DIR on startup, picks the most recent
predictions_*.json, and loads the rest of the JSON/CSV artifacts the API
serves. Missing files are warned about, not fatal.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger("loader")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _resolve_data_dir() -> Path:
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "mlb_hr_pipeline" / "data",
        here.parent / "data",
        here / "data",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return candidates[0].resolve()


DATA_DIR = _resolve_data_dir()

_state: dict[str, Any] = {}
_lock = threading.Lock()


PRED_RE = re.compile(r"predictions_(\d{4}-\d{2}-\d{2})\.json$")


def _find_predictions() -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not DATA_DIR.exists():
        return out
    for p in DATA_DIR.glob("predictions_*.json"):
        m = PRED_RE.search(p.name)
        if m:
            out[m.group(1)] = p
    return out


def _safe_json(path: Path) -> Any:
    if not path.exists():
        log.warning("missing: %s", path.name)
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        log.warning("failed to parse %s: %s", path.name, e)
        return None


def _safe_csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        log.warning("missing: %s", path.name)
        return None
    try:
        return pd.read_csv(path)
    except Exception as e:
        log.warning("failed to read %s: %s", path.name, e)
        return None


def _build_pitcher_name_index() -> dict[str, str]:
    """Map normalized pitcher name -> player_id (string), from latest snapshot."""
    idx: dict[str, str] = {}
    snap_root = DATA_DIR / "snapshots"
    if not snap_root.exists():
        return idx
    days = sorted([d for d in snap_root.iterdir() if d.is_dir()], reverse=True)
    for d in days:
        for csv in d.glob("pitchers_*.csv"):
            df = _safe_csv(csv)
            if df is None:
                continue
            name_col = "last_name, first_name"
            if name_col not in df.columns or "player_id" not in df.columns:
                continue
            for _, row in df.iterrows():
                raw = row[name_col]
                pid = row["player_id"]
                if pd.isna(raw) or pd.isna(pid):
                    continue
                parts = [p.strip() for p in str(raw).split(",")]
                if len(parts) != 2:
                    continue
                full = f"{parts[1]} {parts[0]}".lower()
                idx.setdefault(full, str(int(pid)))
        if idx:
            break
    return idx


BATTER_STAT_COLS = {
    "barrel_pct": "barrel_batted_rate",
    "hardhit_pct": "hard_hit_percent",
    "xslg": "xslg",
    "xba": "xba",
    "xwoba": "xwoba",
    "ev": "exit_velocity_avg",
    "la": "launch_angle_avg",
    "whiff_pct": "whiff_percent",
    "k_pct": "k_percent",
    "bb_pct": "bb_percent",
}


def _build_batter_index() -> dict[str, dict]:
    """Map normalized batter name -> {player_id, stats:{...}} from latest snapshot."""
    idx: dict[str, dict] = {}
    snap_root = DATA_DIR / "snapshots"
    if not snap_root.exists():
        return idx
    days = sorted([d for d in snap_root.iterdir() if d.is_dir()], reverse=True)
    for d in days:
        for csv in d.glob("batters_*.csv"):
            df = _safe_csv(csv)
            if df is None:
                continue
            name_col = "last_name, first_name"
            if name_col not in df.columns or "player_id" not in df.columns:
                continue
            for _, row in df.iterrows():
                raw = row[name_col]
                pid = row["player_id"]
                if pd.isna(raw) or pd.isna(pid):
                    continue
                parts = [p.strip() for p in str(raw).split(",")]
                if len(parts) != 2:
                    continue
                full = f"{parts[1]} {parts[0]}".lower()
                if full in idx:
                    continue
                stats: dict[str, float | None] = {}
                for out_key, csv_col in BATTER_STAT_COLS.items():
                    if csv_col in df.columns:
                        val = row[csv_col]
                        stats[out_key] = None if pd.isna(val) else float(val)
                    else:
                        stats[out_key] = None
                idx[full] = {"player_id": str(int(pid)), "stats": stats}
        if idx:
            break
    return idx


def _compute_stale(predictions_date: str | None) -> tuple[bool, str]:
    if not predictions_date:
        return True, "no_predictions_loaded"
    try:
        pd_date = date.fromisoformat(predictions_date)
    except ValueError:
        return True, "bad_date"
    today = date.today()
    delta = (today - pd_date).days
    if delta <= 0:
        return False, "fresh"
    if delta <= 1:
        return True, "stale_one_day"
    if delta <= 3:
        return True, f"stale_{delta}_days"
    return True, f"very_stale_{delta}_days"


def load_all() -> None:
    """Read every artifact from DATA_DIR into the module cache."""
    with _lock:
        log.info("loading data from %s", DATA_DIR)
        preds_by_date = _find_predictions()
        latest_date = max(preds_by_date) if preds_by_date else None
        predictions: dict[str, Any] = {}
        for d, p in preds_by_date.items():
            blob = _safe_json(p)
            if blob:
                predictions[d] = blob

        stale, stale_reason = _compute_stale(latest_date)
        if stale_reason.startswith("very_stale"):
            log.error("predictions are %s old", stale_reason)
        elif stale:
            log.warning("predictions are %s", stale_reason)

        cal_log = _safe_csv(DATA_DIR / "calibration_log.csv")
        cal_metrics = _safe_json(DATA_DIR / "calibration_metrics.json")
        park = _safe_json(DATA_DIR / "park_factors.json")
        platoon = _safe_json(DATA_DIR / "platoon_factors.json")
        counts = _safe_json(DATA_DIR / "count_factors.json")
        zone_league = _safe_json(DATA_DIR / "zone_hr_league.json")
        zone_maps = _safe_json(DATA_DIR / "zone_hr_maps.json")
        zone_by_pitch = _safe_json(DATA_DIR / "zone_hr_by_pitch.json")
        trajectories = _safe_json(DATA_DIR / "trajectory_arcs.json")
        pitcher_tend = _safe_json(DATA_DIR / "pitcher_zone_tendency.json")

        pitcher_name_idx = _build_pitcher_name_index()
        batter_idx = _build_batter_index()
        batter_name_idx = {n: e["player_id"] for n, e in batter_idx.items()}

        _state.clear()
        _state.update({
            "data_dir": str(DATA_DIR),
            "predictions_by_date": predictions,
            "latest_date": latest_date,
            "stale": stale,
            "stale_reason": stale_reason,
            "loaded_at": datetime.utcnow().isoformat() + "Z",
            "calibration_log": cal_log,
            "calibration_metrics": cal_metrics,
            "park_factors": park,
            "platoon_factors": platoon,
            "count_factors": counts,
            "zone_hr_league": zone_league,
            "zone_hr_maps": zone_maps,
            "zone_hr_by_pitch": zone_by_pitch,
            "trajectory_arcs": trajectories,
            "pitcher_zone_tendency": pitcher_tend,
            "pitcher_name_index": pitcher_name_idx,
            "batter_name_index": batter_name_idx,
            "batter_index": batter_idx,
        })
        log.info(
            "loaded: %d prediction days, latest=%s, stale=%s, cal_rows=%s",
            len(predictions), latest_date, stale_reason,
            0 if cal_log is None else len(cal_log),
        )


def get_data() -> dict[str, Any]:
    if not _state:
        load_all()
    return _state


def get_predictions_for(date_str: str | None) -> tuple[dict | None, str | None, bool]:
    """Return (blob, served_date, stale_flag).

    If exact date missing, fall back to latest available and mark stale.
    """
    d = get_data()
    preds = d.get("predictions_by_date") or {}
    if not preds:
        return None, None, True
    if date_str and date_str in preds:
        return preds[date_str], date_str, False
    latest = d.get("latest_date")
    if latest:
        return preds[latest], latest, True
    return None, None, True
