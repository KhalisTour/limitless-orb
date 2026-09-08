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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    # When run as a package (uvicorn backend.main:app, Root Directory = repo root)
    from .loader import DATA_DIR, get_data, get_predictions_for, load_all
except ImportError:
    # When run as a top-level module (uvicorn main:app, Root Directory = backend)
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


# --- data-quality gating ---------------------------------------------------
#
# A side is only "publishable" when the lineup it was built from is fully posted
# AND the opposing starter was priced off his own measured performance. Both
# conditions come from ingest_live via predictions JSON.
#
# Why it matters for the top-picks board specifically: the board is a ranking,
# so anything that inflates one hitter's number relative to the field floats to
# the top. A half-posted lineup does exactly that (fewer hitters, and the ones
# posted first are the ones a team announces early), and a starter regressed to
# a league-average prior is a prediction about a generic pitcher — those hitters
# get a matchup term built from priors rather than from the man on the mound.
# Neither belongs in a list whose whole purpose is "these are the best bets".


def _side_quality(side: dict) -> dict:
    """Publishability of one game-side. `known` is False for pre-gating files."""
    dq = side.get("data_quality") if isinstance(side, dict) else None
    if not isinstance(dq, dict) or not dq:
        return {"known": False, "publishable": True, "reasons": []}
    lineup = dq.get("lineup") or {}
    pitcher = dq.get("pitcher") or {}
    reasons = []
    if lineup.get("confirmed") is False:
        reasons.append("lineup_not_posted")
    if pitcher.get("regressed"):
        reasons.append("pitcher_regressed")
    return {
        "known": True,
        "publishable": not reasons,
        "reasons": reasons,
        "lineup_confirmed": lineup.get("confirmed"),
        "lineup_slots": lineup.get("n_slots"),
        "lineup_hitters": lineup.get("n_hitters"),
        "pitcher_level": pitcher.get("level"),
        "pitcher_regressed": pitcher.get("regressed"),
        "pitcher_n_pa": pitcher.get("n_pa"),
    }


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
            quality = _side_quality(side)
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
                    "tb": entry.get("tb"),
                    "xbh": entry.get("xbh"),
                    "hit": entry.get("hit"),
                    "quality": quality,
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
    """Best hitter in one game, for the schedule card.

    Unlike /api/top-picks this does not drop provisional sides — a game card
    should still show something for a game whose lineups are pending — but it
    flags them so the card can mark the pick as not yet final.
    """
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
                best = {"name": name, "side": side_key, "p_per_pa": p,
                        "provisional": not _side_quality(side).get("publishable", True)}
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
                "tb": entry.get("tb"),
                "xbh": entry.get("xbh"),
                "hit": entry.get("hit"),
            })
        out_sides[side_key] = {
            "pitcher": side.get("pitcher"),
            "hitters": hitters_out,
            "sim": sim,
            # Same signal the top-picks board gates on, surfaced here so a game
            # page can explain why a side's hitters are missing from the board.
            "quality": _side_quality(side),
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
    include_provisional: bool = Query(
        default=False,
        description="Include hitters whose lineup is not fully posted or whose "
                    "opposing starter was regressed to a prior."),
) -> dict:
    flat, blob, served, stale = _gather_hitters_for_date(date or _today_str())
    if not flat:
        return _sanitize({"date": served, "stale": stale, "picks": [],
                          "excluded": {"total": 0, "lineup_not_posted": 0,
                                       "pitcher_regressed": 0},
                          "gating": "none"})

    # Count what the gate removes before removing it, so the page can say
    # "12 hitters held back, lineups pending" rather than just showing fewer rows.
    excluded = {"total": 0, "lineup_not_posted": 0, "pitcher_regressed": 0}
    gated = [h for h in flat if (h.get("quality") or {}).get("known")]
    if not include_provisional:
        kept = []
        for h in flat:
            q = h.get("quality") or {}
            if q.get("publishable", True):
                kept.append(h)
                continue
            excluded["total"] += 1
            for r in q.get("reasons", []):
                excluded[r] = excluded.get(r, 0) + 1
        flat = kept

    # Percentiles rank a hitter against the field that is actually shown.
    all_p = [h["p_per_pa"] for h in flat if h.get("p_per_pa") is not None]
    flat.sort(key=lambda h: (h.get("p_per_pa") or -1.0), reverse=True)
    picks = []
    for rank, h in enumerate(flat[:n], start=1):
        q = h.get("quality") or {}
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
            "xbh": h.get("xbh"),
            "tb": h.get("tb"),
            "hit": h.get("hit"),
            "provisional": not q.get("publishable", True),
            "provisional_reasons": q.get("reasons", []),
        })
    return _sanitize({
        "date": served, "stale": stale, "picks": picks,
        "excluded": excluded,
        # "none" means this date's predictions predate data-quality tracking, so
        # nothing could be gated and the board is unfiltered. Say so rather than
        # implying a filter ran.
        "gating": ("off" if include_provisional
                   else ("on" if gated else "none")),
    })


# ---------------------------------------------------------------------------
# slate context (label thresholds + stat percentile curves)


SLATE_STAT_KEYS = ("barrel_pct", "hardhit_pct", "xslg", "ev",
                   "la", "whiff_pct", "k_pct", "bb_pct")


def _quantile(sorted_asc: list[float], q: float) -> float | None:
    if not sorted_asc:
        return None
    pos = (len(sorted_asc) - 1) * q
    base = int(math.floor(pos))
    rest = pos - base
    if base + 1 < len(sorted_asc):
        return sorted_asc[base] + rest * (sorted_asc[base + 1] - sorted_asc[base])
    return sorted_asc[base]


def _breakpoints(sorted_asc: list[float], n: int = 101) -> list[float]:
    """Percentile curve: n evenly spaced quantiles, 0th..100th."""
    if not sorted_asc:
        return []
    return [_quantile(sorted_asc, i / (n - 1)) for i in range(n)]


@app.get("/api/slate-context")
def slate_context(date: str | None = Query(default=None)) -> dict:
    """Label thresholds and stat percentile curves for a whole slate.

    The frontend needs these to place a hitter against the field, and it used to
    get them by fetching /api/top-picks?n=200 in the root layout — on every page
    render. Three things were wrong with that:

      * n is capped at 200 and a real slate has 235-260 hitters, so every
        quantile was computed on the top 200 by probability with the weakest
        hitters silently dropped, shifting all three thresholds up.
      * once /api/top-picks started gating unposted lineups and regressed
        starters, the thresholds came from the publishable subset while game
        pages and Pick'em label the full slate against them.
      * it shipped ~254 KB of hitter objects (tensor breakdowns, explanations,
        stats) per page render to derive about a dozen numbers.

    This computes them server-side over the ENTIRE ungated slate and returns a
    few KB. Ungated on purpose: the thresholds describe the field, and a hitter
    whose lineup is not posted is still part of the field.
    """
    flat, _blob, served, stale = _gather_hitters_for_date(date or _today_str())

    ps = sorted(h["p_per_pa"] for h in flat if h.get("p_per_pa") is not None)
    thresholds = {
        "elite": _quantile(ps, 0.90),
        "high": _quantile(ps, 0.75),
        "med": _quantile(ps, 0.50),
    }

    stat_curves: dict[str, list[float]] = {}
    for key in SLATE_STAT_KEYS:
        vals = sorted(
            v for v in (
                (h.get("stats") or {}).get(key) for h in flat
            )
            if isinstance(v, (int, float)) and not math.isnan(float(v))
        )
        stat_curves[key] = _breakpoints([float(v) for v in vals])

    return _sanitize({
        "date": served,
        "stale": stale,
        "n_hitters": len(flat),
        "thresholds": thresholds,
        "stat_percentiles": stat_curves,
    })


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


def _per_game_p(df: pd.DataFrame) -> pd.Series:
    """Per-GAME HR probability for each logged prediction.

    The log carries both p_per_pa (per plate appearance) and p_hr (per game),
    and actual_hr is a per-GAME 0/1. Comparing p_per_pa to it — which the
    accuracy page did — is a unit mismatch: a per-PA rate is roughly a quarter
    of the per-game one, so the page reported the model under-predicting by ~3x
    when it was in fact over-predicting by 24%. The one screen whose job is to
    say whether the models work was reporting the bias with the wrong sign.
    """
    if "p_hr" in df.columns and df["p_hr"].notna().any():
        p = df["p_hr"].astype(float)
        if p.isna().any() and "p_per_pa" in df.columns:
            exp_pa = df.get("exp_pa", pd.Series(4.2, index=df.index)).fillna(4.2).astype(float)
            fallback = 1.0 - (1.0 - df["p_per_pa"].astype(float).clip(0, 1)) ** exp_pa
            p = p.fillna(fallback)
        return p
    exp_pa = df.get("exp_pa", pd.Series(4.2, index=df.index)).fillna(4.2).astype(float)
    return 1.0 - (1.0 - df["p_per_pa"].astype(float).clip(0, 1)) ** exp_pa


def _calibration_bins(df: pd.DataFrame) -> list[dict]:
    # Bins are on the per-GAME scale, where the league average is ~12%.
    bounds = [(0.0, 0.06), (0.06, 0.10), (0.10, 0.14), (0.14, 0.20), (0.20, 1.0)]
    labels = ["0-6%", "6-10%", "10-14%", "14-20%", "20%+"]
    out = []
    p = _per_game_p(df)
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

    # Prefer rows produced by the model that is actually running. The log spans
    # model generations, and pooling them makes this page a verdict on whichever
    # generation has the most rows — right after a model change, that is the old
    # one, for weeks. Fall back to the mixed history while the new generation is
    # still filling in, and say which is being shown.
    generation = None
    generation_only = False
    if "model_generation" in recent.columns:
        gens = recent["model_generation"].dropna()
        if not gens.empty:
            generation = int(gens.max())
            cur = recent[recent["model_generation"] == generation]
            if len(cur) >= 50:
                recent = cur
                generation_only = True

    if len(recent) < 50:
        return _sanitize({"status": "insufficient_data", "n": int(len(recent)),
                          "period_days": days, "model_generation": generation})

    recent = recent.copy()
    recent["p_game"] = _per_game_p(recent)

    n_pred = int(len(recent))
    n_hrs = int(recent["actual_hr"].sum())
    rate_pred = float(recent["p_game"].mean())
    rate_actual = float(recent["actual_hr"].mean())
    brier = float(((recent["p_game"] - recent["actual_hr"]) ** 2).mean())

    # AUC and log-loss are computed over the same window as everything else.
    # They used to be read out of calibration_metrics.json, which is a
    # whole-history summary, so the page mixed a 30-day count and Brier with
    # all-time AUC and log-loss and presented them as one number set.
    y = recent["actual_hr"].astype(int).to_numpy()
    p = recent["p_game"].astype(float).clip(1e-6, 1 - 1e-6).to_numpy()
    log_loss = float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())
    auc = None
    if len(set(y.tolist())) >= 2:
        order = np.argsort(p)
        ranks = np.empty(len(p), dtype=float)
        ranks[order] = np.arange(1, len(p) + 1)
        n_pos, n_neg = int(y.sum()), int((1 - y).sum())
        auc = float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))

    # A model that cannot beat "predict the league rate for everyone" is not
    # adding information, however good its Brier looks in isolation.
    baseline_brier = float(((rate_actual - recent["actual_hr"]) ** 2).mean())

    hrs = recent[recent["actual_hr"] == 1].sort_values("p_game", ascending=False)
    misses = recent[recent["actual_hr"] == 0].sort_values("p_game", ascending=False)

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
        "baseline_brier": baseline_brier,
        "beats_baseline": brier < baseline_brier,
        "model_generation": generation,
        # False means the window still contains predictions from older model
        # generations, so these numbers are not purely about the running model.
        "current_generation_only": generation_only,
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
        _REFRESH_LOG.append({"ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z", "game_id": game_id, "outcome": "rate_limited"})
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
        "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
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
            "refreshed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
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
# results (pick'em grading) + odds (betting edge)


@app.get("/api/results/{date}")
def get_results(date: str) -> dict:
    """Who actually homered on `date`, keyed by batter_id.

    Derived from the scored calibration_log already in memory — no network.
    batter_id is resolved via the same _hydrate_batter mapping the rest of
    the API uses, so ids line up with the frontend's picks. `final` is True
    once the date has been scored (rows exist).
    """
    d = get_data()
    cal = d.get("calibration_log")
    hr_by_batter_id: dict[str, int] = {}
    final = False
    if cal is not None and len(cal) and "game_date" in cal.columns:
        rows = cal[cal["game_date"].astype(str) == date]
        final = len(rows) > 0
        for r in rows.itertuples(index=False):
            try:
                cnt = int(getattr(r, "actual_hr_count", 0) or 0)
            except (TypeError, ValueError):
                cnt = 0
            if cnt < 1:
                continue
            bid, _ = _hydrate_batter(str(getattr(r, "hitter", "")))
            if bid is not None:
                hr_by_batter_id[str(bid)] = cnt
    return _sanitize({"date": date, "final": final, "hr_by_batter_id": hr_by_batter_id})


def _american_to_implied(american: Any) -> float | None:
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    return 100.0 / (a + 100.0) if a >= 0 else (-a) / ((-a) + 100.0)


def _american_profit(american: Any) -> float | None:
    """Profit per 1 unit staked at the given American odds."""
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    return a / 100.0 if a >= 0 else 100.0 / (-a)


@app.get("/api/odds/{date}")
def get_odds(date: str) -> dict:
    """HR-prop odds for `date` (written by the pipeline's odds fetch), joined
    with the model's p_game_hr to compute edge and EV per hitter.

    available:false when no odds file exists yet (e.g. the fetch was blocked).
    """
    d = get_data()
    odds_blob = (d.get("odds_by_date") or {}).get(date)

    hitters, _, _, _ = _gather_hitters_for_date(date)
    p_by_id: dict[str, float] = {}
    p_by_name: dict[str, float] = {}
    for h in hitters:
        pg = h.get("p_game_hr")
        if pg is None:
            continue
        if h.get("batter_id") is not None:
            p_by_id[str(h["batter_id"])] = pg
        if h.get("name"):
            p_by_name[str(h["name"]).lower()] = pg

    if not odds_blob:
        return _sanitize({"date": date, "available": False, "book": None, "odds": []})

    # One line per batter, anytime-HR only.
    #
    # The-Odds-API's batter_home_runs market returns several thresholds for the
    # same player — 0.5 (anytime), 1.5 (2+), 2.5 (3+) — and the fetcher used to
    # store all of them with the threshold stripped off. Only the anytime price
    # is comparable to p_game_hr, so a +16000 3-HR line was being scored against
    # P(at least one HR) and reported as a 57% edge. Worse, nothing deduped, so
    # which line a page displayed came down to iteration order: the same hitter
    # showed +32% edge on the board and +57% on his matchup page.
    #
    # Newer odds files carry `point`; older ones do not, so fall back to keeping
    # the shortest price per batter, which is the anytime market by construction.
    raw_lines = list(odds_blob.get("lines") or [])
    typed = [ln for ln in raw_lines if ln.get("point") is not None]
    if typed:
        raw_lines = [ln for ln in typed if abs(float(ln["point"]) - 0.5) < 1e-6]
    best: dict[str, dict] = {}
    for ln in raw_lines:
        key = str(ln.get("batter_id") or (ln.get("name") or "").lower())
        if not key:
            continue
        cur = best.get(key)
        a = ln.get("american")
        if a is None:
            continue
        if cur is None or a < cur.get("american", float("inf")):
            best[key] = ln

    out: list[dict] = []
    for ln in best.values():
        bid = ln.get("batter_id")
        name = ln.get("name")
        american = ln.get("american")
        implied = _american_to_implied(american)
        model = None
        if bid is not None and str(bid) in p_by_id:
            model = p_by_id[str(bid)]
        elif name and name.lower() in p_by_name:
            model = p_by_name[name.lower()]
        edge = ev = None
        profit = _american_profit(american)
        if model is not None and implied is not None and profit is not None:
            edge = model - implied
            ev = model * profit - (1.0 - model)
        out.append({
            "batter_id": str(bid) if bid is not None else None,
            "name": name,
            "american": american,
            "implied": implied,
            "model_p": model,
            "edge": edge,
            "ev": ev,
        })

    out.sort(key=lambda x: (x["edge"] is not None, x["edge"] or 0.0), reverse=True)
    return _sanitize({
        "date": date,
        "available": True,
        "book": odds_blob.get("book"),
        "pulled_at": odds_blob.get("pulled_at"),
        "odds": out,
    })


# ---------------------------------------------------------------------------
# error handling


@app.exception_handler(Exception)
async def _unhandled(_request: Request, exc: Exception):
    log.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "internal error", "type": type(exc).__name__})
