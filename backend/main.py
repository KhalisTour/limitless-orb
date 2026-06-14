"""FastAPI server for the MLB HR pipeline.

Read-only over the artifacts in DATA_DIR, plus a refresh endpoint that
shells out to predict_today.py and a reload endpoint that re-scans disk.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from loader import DATA_DIR, get_data, get_predictions_for, load_all

log = logging.getLogger("api")

app = FastAPI(title="MLB HR Pipeline API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=r"https?://(localhost(:\d+)?|.*\.vercel\.app)",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Data-Date"],
)


@app.on_event("startup")
def _startup() -> None:
    load_all()


@app.middleware("http")
async def _add_data_date_header(request: Request, call_next):
    response: Response = await call_next(request)
    d = get_data()
    if d.get("latest_date"):
        response.headers["X-Data-Date"] = d["latest_date"]
    return response


def _today_str() -> str:
    return date.today().isoformat()


def _sanitize(o: Any) -> Any:
    """JSON-safe coercion: NaN -> None, numpy scalars -> Python scalars."""
    if isinstance(o, dict):
        return {str(k): _sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sanitize(v) for v in o]
    if isinstance(o, tuple):
        return [_sanitize(v) for v in o]
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
        return o
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            return _sanitize(o.item())
        except Exception:
            return str(o)
    return o


# ---------------------------------------------------------------------------
# helpers


def _hydrate_batter(name: str) -> tuple[str | None, dict]:
    """Return (batter_id, stats) for a hitter name, or (None, {}) if unknown."""
    d = get_data()
    idx = d.get("batter_index") or {}
    entry = idx.get(name.lower())
    if not entry:
        # Try last-token fallback (handles "Julio Rodríguez" vs ascii variants).
        return None, {k: None for k in (
            "barrel_pct", "hardhit_pct", "xslg", "xba", "xwoba",
            "ev", "la", "whiff_pct", "k_pct", "bb_pct",
        )}
    return entry.get("player_id"), entry.get("stats") or {}


def _gather_hitters_for_date(date_str: str | None) -> tuple[list[dict], dict, str | None, bool]:
    """Flatten all hitters across all games on a date.

    Returns (hitters_list, blob, served_date, stale).
    """
    blob, served, stale = get_predictions_for(date_str)
    flat: list[dict] = []
    if not blob:
        return flat, {}, served, stale
    for g in blob.get("games", []):
        sides = g.get("sides") or {}
        for side_key in ("away", "home"):
            side = sides.get(side_key)
            if not isinstance(side, dict) or side.get("error"):
                continue
            opp_pitcher = side.get("pitcher")
            per_hitter = side.get("per_hitter") or {}
            sim = side.get("sim") or {}
            p_game_lookup = (sim.get("p_at_least_one_hr") or {}) if isinstance(sim, dict) else {}
            p_multi_lookup = (sim.get("p_multi_hr") or {}) if isinstance(sim, dict) else {}
            for slot, (name, entry) in enumerate(per_hitter.items(), start=1):
                if not isinstance(entry, dict) or "error" in entry:
                    continue
                batter_id, stats = _hydrate_batter(name)
                flat.append({
                    "game_id": g.get("game_id"),
                    "away_team": g.get("away"),
                    "home_team": g.get("home"),
                    "away_sp": g.get("away_sp"),
                    "home_sp": g.get("home_sp"),
                    "game_datetime": g.get("game_datetime"),
                    "side": side_key,
                    "name": name,
                    "batter_id": batter_id,
                    "lineup_slot": slot,
                    "p_per_pa": entry.get("p_per_pa"),
                    "p_game_hr": p_game_lookup.get(name),
                    "p_multi_hr": p_multi_lookup.get(name),
                    "exp_pa": entry.get("exp_pa"),
                    "components": entry.get("components"),
                    "tto_mult": entry.get("tto_mult"),
                    "explanation": entry.get("explanation"),
                    "opp_pitcher": opp_pitcher,
                    "stats": stats,
                })
    return flat, blob, served, stale


def _pctile_rank(values: list[float], x: float | None) -> int | None:
    if x is None or not values:
        return None
    cleaner = [v for v in values if v is not None]
    if not cleaner:
        return None
    n = len(cleaner)
    less = sum(1 for v in cleaner if v < x)
    return int(round(100.0 * less / n))


def _top_pick_for_game(game: dict) -> dict | None:
    sides = game.get("sides") or {}
    best = None
    for side_key in ("away", "home"):
        side = sides.get(side_key)
        if not isinstance(side, dict):
            continue
        for name, entry in (side.get("per_hitter") or {}).items():
            if not isinstance(entry, dict) or "error" in entry:
                continue
            p = entry.get("p_per_pa")
            if p is None:
                continue
            if best is None or p > best["p_per_pa"]:
                best = {"name": name, "side": side_key, "p_per_pa": p}
    return best


# ---------------------------------------------------------------------------
# health


@app.get("/api/health")
def health() -> dict:
    d = get_data()
    n_games = 0
    if d.get("latest_date"):
        blob = (d.get("predictions_by_date") or {}).get(d["latest_date"]) or {}
        n_games = len(blob.get("games", []))
    return {
        "status": "ok",
        "predictions_date": d.get("latest_date"),
        "n_games": n_games,
        "last_loaded": d.get("loaded_at"),
        "stale": d.get("stale"),
        "stale_reason": d.get("stale_reason"),
        "data_dir": d.get("data_dir"),
    }
# ---------------------------------------------------------------------------
# dates list


@app.get("/api/dates")
def list_dates() -> dict:
    """List dates for which predictions JSON exists, newest first."""
    d = get_data()
    preds = d.get("predictions_by_date") or {}
    dates = sorted(preds.keys(), reverse=True)
    return {
        "dates": dates,
        "latest": d.get("latest_date"),
        "n": len(dates),
    }

    
# ---------------------------------------------------------------------------
# games list


@app.get("/api/games")
def list_games(date: str | None = Query(default=None, description="YYYY-MM-DD")) -> dict:
    blob, served, stale = get_predictions_for(date or _today_str())
    if not blob:
        raise HTTPException(status_code=503, detail="no predictions available")
    games_out = []
    for g in blob.get("games", []):
        games_out.append({
            "game_id": g.get("game_id"),
            "away_team": g.get("away"),
            "home_team": g.get("home"),
            "away_sp": g.get("away_sp"),
            "home_sp": g.get("home_sp"),
            "game_datetime": g.get("game_datetime"),
            "venue": g.get("venue"),
            "park_factor": g.get("park_factor"),
            "top_pick": _top_pick_for_game(g),
        })
    return _sanitize({"date": served, "stale": stale, "games": games_out})


# ---------------------------------------------------------------------------
# single game


@app.get("/api/game/{game_id}")
def get_game(game_id: int, date: str | None = Query(default=None)) -> dict:
    blob, served, stale = get_predictions_for(date or _today_str())
    if not blob:
        raise HTTPException(status_code=503, detail="no predictions available")
    target = None
    for g in blob.get("games", []):
        if g.get("game_id") == game_id:
            target = g
            break
    if target is None:
        raise HTTPException(status_code=404, detail=f"game {game_id} not found on {served}")

    all_p = [
        h["p_per_pa"]
        for h in _gather_hitters_for_date(served)[0]
        if h.get("p_per_pa") is not None
    ]

    out_sides: dict[str, Any] = {}
    for side_key in ("away", "home"):
        side = (target.get("sides") or {}).get(side_key)
        if not isinstance(side, dict) or "error" in side:
            out_sides[side_key] = {
                "error": (side or {}).get("error") if isinstance(side, dict) else "missing"
            }
            continue
        per_hitter = side.get("per_hitter") or {}
        sim = side.get("sim") or {}
        p_game_lookup = (sim.get("p_at_least_one_hr") or {}) if isinstance(sim, dict) else {}
        p_multi_lookup = (sim.get("p_multi_hr") or {}) if isinstance(sim, dict) else {}
        hitters_out: list[dict] = []
        for slot, (name, entry) in enumerate(per_hitter.items(), start=1):
            if not isinstance(entry, dict) or "error" in entry:
                hitters_out.append({
                    "name": name,
                    "lineup_slot": slot,
                    "error": (entry or {}).get("error"),
                })
                continue
            p = entry.get("p_per_pa")
            batter_id, stats = _hydrate_batter(name)
            hitters_out.append({
                "name": name,
                "batter_id": batter_id,
                "lineup_slot": slot,
                "p_per_pa": p,
                "p_per_pa_pctile": _pctile_rank(all_p, p),
                "p_game_hr": p_game_lookup.get(name),
                "p_multi_hr": p_multi_lookup.get(name),
                "components": entry.get("components"),
                "tto_mult": entry.get("tto_mult"),
                "exp_pa": entry.get("exp_pa"),
                "park_factor": entry.get("park_factor"),
                "platoon_factor": entry.get("platoon_factor"),
                "explanation": entry.get("explanation"),
                "stats": stats,
            })
        out_sides[side_key] = {
            "pitcher": side.get("pitcher"),
            "hitters": hitters_out,
            "sim": sim,
        }

    return _sanitize({
        "game_id": target.get("game_id"),
        "date": served,
        "stale": stale,
        "away_team": target.get("away"),
        "home_team": target.get("home"),
        "away_sp": target.get("away_sp"),
        "home_sp": target.get("home_sp"),
        "game_datetime": target.get("game_datetime"),
        "venue": target.get("venue"),
        "park_factor": target.get("park_factor"),
        "sides": out_sides,
    })


# ---------------------------------------------------------------------------
# top picks


@app.get("/api/top-picks")
def top_picks(
    date: str | None = Query(default=None),
    n: int = Query(default=10, ge=1, le=200),
) -> dict:
    flat, blob, served, stale = _gather_hitters_for_date(date or _today_str())
    if not flat:
        return _sanitize({"date": served, "stale": stale, "picks": []})
    all_p = [h["p_per_pa"] for h in flat if h.get("p_per_pa") is not None]
    flat.sort(key=lambda h: (h.get("p_per_pa") or -1.0), reverse=True)
    picks = []
    for rank, h in enumerate(flat[:n], start=1):
        picks.append({
            "rank": rank,
            "name": h["name"],
            "batter_id": h.get("batter_id"),
            "side": h["side"],
            "game_id": h["game_id"],
            "away_team": h["away_team"],
            "home_team": h["home_team"],
            "game_datetime": h.get("game_datetime"),
            "opp_pitcher": h["opp_pitcher"],
            "p_per_pa": h["p_per_pa"],
            "p_per_pa_pctile": _pctile_rank(all_p, h["p_per_pa"]),
            "p_game_hr": h.get("p_game_hr"),
            "p_multi_hr": h.get("p_multi_hr"),
            "components": h["components"],
            "lineup_slot": h["lineup_slot"],
            "exp_pa": h["exp_pa"],
            "stats": h.get("stats"),
        })
    return _sanitize({"date": served, "stale": stale, "picks": picks})


# ---------------------------------------------------------------------------
# zones


def _lookup_id(blob: dict | None, player_id: int) -> Any | None:
    if not isinstance(blob, dict):
        return None
    if str(player_id) in blob:
        return blob[str(player_id)]
    if player_id in blob:
        return blob[player_id]
    return None


def _filter_family(blob: dict, pitch_family: str) -> dict:
    """For zone_hr_by_pitch entries shaped {name, fastball:{}, breaking:{}, ...}."""
    if pitch_family == "all":
        merged: dict[str, dict] = {}
        for fam in ("fastball", "breaking", "offspeed"):
            zd = blob.get(fam) or {}
            for z, v in zd.items():
                merged.setdefault(z, {"hr_pct": 0.0, "avg_la": 0.0, "avg_ev": 0.0, "n": 0})
                n_old = merged[z]["n"]
                n_add = v.get("n", 0) or 0
                tot = n_old + n_add
                if tot > 0:
                    merged[z]["hr_pct"] = (
                        (merged[z]["hr_pct"] * n_old + (v.get("hr_pct") or 0.0) * n_add) / tot
                    )
                    merged[z]["avg_la"] = (
                        (merged[z]["avg_la"] * n_old + (v.get("avg_la") or 0.0) * n_add) / tot
                    )
                    merged[z]["avg_ev"] = (
                        (merged[z]["avg_ev"] * n_old + (v.get("avg_ev") or 0.0) * n_add) / tot
                    )
                merged[z]["n"] = tot
        return merged
    return blob.get(pitch_family) or {}


def _league_baseline() -> dict:
    d = get_data()
    zl = d.get("zone_hr_league") or {}
    return zl if isinstance(zl, dict) else {}


@app.get("/api/zones/{player_id}")
def get_zones(
    player_id: int,
    pitch_family: str = Query(default="all", regex="^(all|fastball|breaking|offspeed)$"),
) -> dict:
    d = get_data()
    league = _league_baseline()

    if pitch_family == "all":
        zmap = d.get("zone_hr_maps")
        entry = _lookup_id(zmap, player_id)
        if entry is None:
            return _sanitize({
                "player_id": player_id,
                "name": None,
                "pitch_family": pitch_family,
                "player_found": False,
                "zones": {},
                "league_baseline": league,
            })
        return _sanitize({
            "player_id": player_id,
            "name": entry.get("name"),
            "pitch_family": pitch_family,
            "player_found": True,
            "zones": entry.get("zones") or {},
            "league_baseline": league,
        })

    by_pitch = d.get("zone_hr_by_pitch")
    entry = _lookup_id(by_pitch, player_id)
    if entry is None:
        return _sanitize({
            "player_id": player_id,
            "name": None,
            "pitch_family": pitch_family,
            "player_found": False,
            "zones": {},
            "league_baseline": league,
        })
    return _sanitize({
        "player_id": player_id,
        "name": entry.get("name"),
        "pitch_family": pitch_family,
        "player_found": True,
        "zones": entry.get(pitch_family) or {},
        "league_baseline": league,
    })


# ---------------------------------------------------------------------------
# matchup


def _resolve_pitcher_id(name: str) -> str | None:
    d = get_data()
    idx = d.get("pitcher_name_index") or {}
    return idx.get(name.lower())


def _pitcher_tendency_for(pid: str | None, pitch_family: str) -> tuple[dict, dict | None]:
    """Return (zone_pct_map, raw_entry)."""
    if not pid:
        return {}, None
    d = get_data()
    pt = d.get("pitcher_zone_tendency") or {}
    entry = pt.get(str(pid))
    if not isinstance(entry, dict):
        return {}, None
    fam = entry.get(pitch_family) if pitch_family in ("all", "fastball", "breaking", "offspeed") else None
    if fam is None:
        fam = entry.get("all") or {}
    return fam, entry


@app.get("/api/matchup")
def matchup(
    batter_id: int = Query(...),
    pitcher_name: str = Query(...),
    pitch_family: str = Query(default="all", regex="^(all|fastball|breaking|offspeed)$"),
) -> dict:
    d = get_data()
    # batter zones
    if pitch_family == "all":
        bmap = d.get("zone_hr_maps") or {}
        bentry = _lookup_id(bmap, batter_id)
        batter_zones = (bentry or {}).get("zones") or {}
    else:
        bmap = d.get("zone_hr_by_pitch") or {}
        bentry = _lookup_id(bmap, batter_id)
        batter_zones = (bentry or {}).get(pitch_family) or {}
    batter_name = (bentry or {}).get("name") if bentry else None

    # pitcher tendency
    pid = _resolve_pitcher_id(pitcher_name)
    p_tend_map, p_entry = _pitcher_tendency_for(pid, pitch_family)

    if not batter_zones or not p_tend_map:
        return _sanitize({
            "batter_id": batter_id,
            "batter_name": batter_name,
            "pitcher_name": pitcher_name,
            "pitcher_id": pid,
            "pitch_family": pitch_family,
            "zones": {},
            "hottest_zone": None,
            "hottest_pitch_family": None,
            "narrative": "not_available",
        })

    danger: dict[str, dict[str, float]] = {}
    raw_scores: dict[str, float] = {}
    for z, bz in batter_zones.items():
        if not isinstance(bz, dict):
            continue
        b_hr = bz.get("hr_pct") or 0.0
        p_tend = p_tend_map.get(str(z))
        if p_tend is None:
            p_tend = p_tend_map.get(z) or 0.0
        p_tend = float(p_tend or 0.0)
        score = float(b_hr) * p_tend
        raw_scores[str(z)] = score
        danger[str(z)] = {
            "danger_score": score,
            "batter_hr_pct": float(b_hr),
            "pitcher_tendency": p_tend,
        }
    total = sum(raw_scores.values())
    for z, s in raw_scores.items():
        danger[z]["danger_pct"] = (s / total) if total > 0 else 0.0

    hottest_zone = max(raw_scores, key=raw_scores.get) if raw_scores else None

    # hottest pitch family for this batter overall
    hottest_family = None
    if p_entry:
        best = -1.0
        for fam in ("fastball", "breaking", "offspeed"):
            fam_map = p_entry.get(fam) or {}
            if not fam_map:
                continue
            # weight by batter overall hr_pct per zone
            score = 0.0
            for z, bz in (batter_zones if pitch_family == "all" else (d.get("zone_hr_maps") or {}).get(str(batter_id), {}).get("zones", {})).items():
                if not isinstance(bz, dict):
                    continue
                p = fam_map.get(str(z), 0.0)
                score += float(bz.get("hr_pct") or 0.0) * float(p or 0.0)
            if score > best:
                best = score
                hottest_family = fam

    narrative = "not_available"
    if hottest_zone is not None:
        bz = danger[hottest_zone]
        narrative = (
            f"Pitcher throws {bz['pitcher_tendency'] * 100:.1f}% of pitches to zone {hottest_zone}, "
            f"where batter has a {bz['batter_hr_pct'] * 100:.1f}% HR rate."
        )

    return _sanitize({
        "batter_id": batter_id,
        "batter_name": batter_name,
        "pitcher_name": pitcher_name,
        "pitcher_id": pid,
        "pitch_family": pitch_family,
        "zones": danger,
        "hottest_zone": hottest_zone,
        "hottest_pitch_family": hottest_family,
        "narrative": narrative,
    })


# ---------------------------------------------------------------------------
# trajectory


@app.get("/api/trajectory/{player_id}")
def trajectory(
    player_id: int,
    zone: str = Query(default="5"),
    pitch_family: str = Query(default="fastball", regex="^(fastball|breaking|offspeed|all)$"),
) -> dict:
    d = get_data()
    arcs_blob = d.get("trajectory_arcs") or {}
    entry = _lookup_id(arcs_blob, player_id)
    if not isinstance(entry, dict):
        return _sanitize({
            "player_id": player_id,
            "zone": zone,
            "pitch_family": pitch_family,
            "found": False,
            "trajectory": [],
        })

    arcs = entry.get("arcs") or {}
    key = f"zone_{zone}_{pitch_family}"
    arc = arcs.get(key)
    used_key = key
    if arc is None:
        # Fallback: any arc for this player.
        if arcs:
            used_key = next(iter(arcs.keys()))
            arc = arcs[used_key]
    if arc is None:
        return _sanitize({
            "player_id": player_id,
            "name": entry.get("name"),
            "zone": zone,
            "pitch_family": pitch_family,
            "found": False,
            "trajectory": [],
        })

    return _sanitize({
        "player_id": player_id,
        "name": entry.get("name"),
        "zone": zone,
        "pitch_family": pitch_family,
        "found": True,
        "used_key": used_key,
        "median_la": arc.get("median_la"),
        "median_ev": arc.get("median_ev"),
        "apex_ft": arc.get("apex_ft"),
        "distance_ft": arc.get("distance_ft"),
        "trajectory": arc.get("trajectory") or [],
    })


# ---------------------------------------------------------------------------
# accuracy


def _calibration_bins(df: pd.DataFrame) -> list[dict]:
    bounds = [(0.0, 0.02), (0.02, 0.04), (0.04, 0.06), (0.06, 0.08), (0.08, 1.0)]
    labels = ["0-2%", "2-4%", "4-6%", "6-8%", "8%+"]
    out = []
    p = df["p_per_pa"].astype(float)
    a = df["actual_hr"].astype(float)
    for (lo, hi), label in zip(bounds, labels):
        mask = (p >= lo) & (p < hi) if hi < 1.0 else (p >= lo)
        sub_p = p[mask]
        sub_a = a[mask]
        n = int(len(sub_p))
        out.append({
            "bin": label,
            "predicted_avg": float(sub_p.mean()) if n else 0.0,
            "actual_rate": float(sub_a.mean()) if n else 0.0,
            "n": n,
        })
    return out


@app.get("/api/accuracy")
def accuracy(days: int = Query(default=30, ge=1, le=365)) -> dict:
    d = get_data()
    df = d.get("calibration_log")
    if df is None or len(df) == 0:
        return _sanitize({"status": "insufficient_data", "n": 0, "period_days": days})
    df = df.copy()
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)
    recent = df[df["game_date"] >= cutoff]
    if len(recent) < 50:
        return _sanitize({"status": "insufficient_data", "n": int(len(recent)), "period_days": days})

    n_pred = int(len(recent))
    n_hrs = int(recent["actual_hr"].sum())
    rate_pred = float(recent["p_per_pa"].mean())
    rate_actual = float(recent["actual_hr"].mean())
    brier = float(((recent["p_per_pa"] - recent["actual_hr"]) ** 2).mean())

    metrics = d.get("calibration_metrics") or {}
    auc = metrics.get("auc")
    log_loss = metrics.get("log_loss")

    hrs = recent[recent["actual_hr"] == 1].sort_values("p_per_pa", ascending=False)
    misses = recent[recent["actual_hr"] == 0].sort_values("p_per_pa", ascending=False)

    def _row(r: pd.Series) -> dict:
        return {
            "date": r["game_date"].date().isoformat() if not pd.isna(r["game_date"]) else None,
            "hitter": r.get("hitter"),
            "opp_pitcher": r.get("opp_pitcher"),
            "p_per_pa": float(r.get("p_per_pa", 0.0)),
            "actual_hr": int(r.get("actual_hr", 0)),
        }

    return _sanitize({
        "status": "ok",
        "period_days": days,
        "n_predictions": n_pred,
        "n_hrs": n_hrs,
        "rate_predicted": rate_pred,
        "rate_actual": rate_actual,
        "brier": brier,
        "auc": auc,
        "log_loss": log_loss,
        "calibration_bins": _calibration_bins(recent),
        "best_calls": [_row(r) for _, r in hrs.head(3).iterrows()],
        "worst_misses": [_row(r) for _, r in misses.head(3).iterrows()],
    })


# ---------------------------------------------------------------------------
# refresh + reload


_REFRESH_LOG: list[dict] = []
_LAST_REFRESH: dict[int, float] = {}
_REFRESH_COOLDOWN_S = 10 * 60


def _pipeline_root() -> Path | None:
    p = Path(DATA_DIR).resolve().parent
    if (p / "predict_today.py").exists():
        return p
    here = Path(__file__).resolve().parent.parent / "mlb_hr_pipeline"
    if (here / "predict_today.py").exists():
        return here
    return None


@app.post("/api/refresh/{game_id}")
def refresh_game(game_id: int) -> dict:
    now = time.time()
    last = _LAST_REFRESH.get(game_id, 0.0)
    if now - last < _REFRESH_COOLDOWN_S:
        wait = int(_REFRESH_COOLDOWN_S - (now - last))
        log.info("refresh %s rate-limited (%ds remaining)", game_id, wait)
        _REFRESH_LOG.append({"ts": datetime.utcnow().isoformat() + "Z", "game_id": game_id, "outcome": "rate_limited"})
        # Still return current game state, with a flag.
        try:
            current = get_game(game_id)
        except HTTPException:
            current = {}
        return _sanitize({
            "game_id": game_id,
            "refreshed": False,
            "rate_limited": True,
            "retry_in_s": wait,
            "game": current,
        })

    root = _pipeline_root()
    if root is None:
        raise HTTPException(status_code=500, detail="pipeline scripts not found")

    today = _today_str()
    cmd = ["python", "predict_today.py", today]
    log.info("refresh %s: running %s in %s", game_id, " ".join(cmd), root)
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True, timeout=300, check=False,
        )
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
        proc = None
        log.error("refresh %s: timeout", game_id)
    except Exception as e:
        ok = False
        proc = None
        log.error("refresh %s: %s", game_id, e)

    _LAST_REFRESH[game_id] = now
    _REFRESH_LOG.append({
        "ts": datetime.utcnow().isoformat() + "Z",
        "game_id": game_id,
        "outcome": "ok" if ok else "fail",
    })

    if ok:
        load_all()
        try:
            updated = get_game(game_id)
        except HTTPException:
            updated = {}
        return _sanitize({
            "game_id": game_id,
            "refreshed": True,
            "refreshed_at": datetime.utcnow().isoformat() + "Z",
            "game": updated,
        })

    try:
        current = get_game(game_id)
    except HTTPException:
        current = {}
    return _sanitize({
        "game_id": game_id,
        "refreshed": False,
        "refresh_failed": True,
        "stderr": (proc.stderr[-500:] if proc and proc.stderr else None),
        "game": current,
    })


@app.post("/api/reload")
def reload_now() -> dict:
    load_all()
    d = get_data()
    return _sanitize({
        "reloaded": True,
        "predictions_date": d.get("latest_date"),
        "loaded_at": d.get("loaded_at"),
        "n_dates": len(d.get("predictions_by_date") or {}),
    })


@app.get("/api/refresh/log")
def refresh_log() -> dict:
    return {"entries": _REFRESH_LOG[-50:]}


# ---------------------------------------------------------------------------
# error handling


@app.exception_handler(Exception)
async def _unhandled(_request: Request, exc: Exception):
    log.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "internal error", "type": type(exc).__name__})
