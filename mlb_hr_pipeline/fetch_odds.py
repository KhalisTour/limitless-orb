"""
Phase 5b — pull HR-prop odds via The-Odds-API and write data/odds_<date>.json.

Source: the-odds-api.com free tier (500 req/month).
API key reads from env var ODDS_API_KEY; falls back to embedded default.

Only fetches events that contain today's top N picks, so the free quota goes
toward actionable lines rather than the full slate (~3-8 requests vs ~15).
Output filters to those same players, so the EdgeBadge only shows lines for
picks that actually appear on the Top Picks screen.

Never raises — exits 0 so the pipeline keeps running even if odds fail.

Usage:
  python fetch_odds.py [YYYY-MM-DD] [--debug] [--top=N]
"""

from __future__ import annotations

import csv
import json
import os
import sys
import unicodedata
import datetime as dt
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
SNAP_DIR = DATA_DIR / "snapshots"

# Env var takes precedence; embedded key is the fallback for local runs.
_EMBEDDED_KEY = "a114608bb9ca00aa6e3013a7bdd370e0"
API_KEY = os.environ.get("ODDS_API_KEY") or _EMBEDDED_KEY

BASE = "https://api.the-odds-api.com/v4"
SPORT = "baseball_mlb"
# HR-prop market key on The-Odds-API. If this ever 404s run --debug to see
# available markets and update this constant.
HR_MARKET = "batter_home_runs"
# Preferred bookmakers in priority order; first one present wins.
BOOK_PRIORITY = ["draftkings", "fanduel", "betmgm", "pointsbetus", "williamhill_us"]
TIMEOUT = 15
DEFAULT_TOP_N = 10


# ---------------------------------------------------------------------------
# HTTP

def _get(url: str, params: dict) -> dict | list | None:
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        remaining = r.headers.get("x-requests-remaining", "?")
        used = r.headers.get("x-requests-used", "?")
        if r.status_code != 200:
            print(f"[odds] HTTP {r.status_code} — {url.split(BASE)[-1]} (used={used} remaining={remaining})", file=sys.stderr)
            return None
        print(f"[odds] ok — {url.split(BASE)[-1]}  quota used={used} remaining={remaining}")
        return r.json()
    except Exception as e:
        print(f"[odds] request error: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Name normalization (handles accents, punctuation differences)

def _norm(name: str) -> str:
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.lower().strip()


# ---------------------------------------------------------------------------
# Read top picks from today's predictions

def _top_picks(date: str, n: int) -> list[dict]:
    pred_path = DATA_DIR / f"predictions_{date}.json"
    if not pred_path.exists():
        print(f"[odds] no predictions_{date}.json — skipping odds fetch", file=sys.stderr)
        return []
    try:
        blob = json.loads(pred_path.read_text())
    except Exception as e:
        print(f"[odds] failed to read predictions: {e}", file=sys.stderr)
        return []

    picks: list[dict] = []
    for game in blob.get("games") or []:
        away = game.get("away", "")
        home = game.get("home", "")
        for side in (game.get("sides") or {}).values():
            if not isinstance(side, dict) or "per_hitter" not in side:
                continue
            for name, h in (side.get("per_hitter") or {}).items():
                if not isinstance(h, dict) or h.get("p_per_pa") is None:
                    continue
                picks.append({"name": name, "p_per_pa": h["p_per_pa"], "away": away, "home": home})

    picks.sort(key=lambda x: x["p_per_pa"], reverse=True)
    top = picks[:n]
    print(f"[odds] top {n} picks: {[p['name'] for p in top]}")
    return top


# ---------------------------------------------------------------------------
# Team matching (The-Odds-API uses full franchise names like "Minnesota Twins")

def _team_match(our: str, api: str) -> bool:
    a, b = _norm(our), _norm(api)
    if a == b:
        return True
    a_words, b_words = set(a.replace(".", "").split()), set(b.replace(".", "").split())
    if not a_words or not b_words:
        return False
    # Match if nickname word matches and ≥1 city word is shared
    return a.split()[-1] == b.split()[-1] and len(a_words & b_words) >= 2


def _match_events(events: list[dict], games: list[dict]) -> list[str]:
    """Return The-Odds-API event IDs whose matchups overlap our top-picks games."""
    ids: list[str] = []
    for ev in events:
        eh, ea = ev.get("home_team", ""), ev.get("away_team", "")
        for g in games:
            if (_team_match(g["home"], eh) and _team_match(g["away"], ea)) or \
               (_team_match(g["home"], ea) and _team_match(g["away"], eh)):
                ids.append(ev["id"])
                break
    return ids


# ---------------------------------------------------------------------------
# Snapshot name → batter_id index

def _name_to_id() -> dict[str, str]:
    idx: dict[str, str] = {}
    if not SNAP_DIR.exists():
        return idx
    for d in sorted((x for x in SNAP_DIR.iterdir() if x.is_dir()), reverse=True):
        for csvp in d.glob("batters_*.csv"):
            try:
                with csvp.open() as fh:
                    for row in csv.DictReader(fh):
                        raw = row.get("last_name, first_name")
                        pid = row.get("player_id")
                        if not raw or not pid:
                            continue
                        parts = [p.strip() for p in raw.split(",")]
                        if len(parts) != 2:
                            continue
                        idx.setdefault(_norm(f"{parts[1]} {parts[0]}"), str(pid).split(".")[0])
            except Exception:
                continue
        if idx:
            break
    return idx


# ---------------------------------------------------------------------------
# Bookmaker selection

def _pick_book(bookmakers: list[dict]) -> tuple[str | None, list[dict]]:
    bm_map = {b["key"]: b for b in bookmakers}
    candidates = list(BOOK_PRIORITY) + [k for k in bm_map if k not in BOOK_PRIORITY]
    for key in candidates:
        bm = bm_map.get(key)
        if not bm:
            continue
        for mkt in bm.get("markets") or []:
            if mkt.get("key") == HR_MARKET:
                return bm["key"], mkt.get("outcomes") or []
    return None, []


# ---------------------------------------------------------------------------
# Main

def main(date: str, debug: bool = False, top_n: int = DEFAULT_TOP_N) -> None:
    picks = _top_picks(date, top_n)
    if not picks:
        return

    target_names = {_norm(p["name"]) for p in picks}
    unique_games = list({(p["away"], p["home"]) for p in picks})
    our_games = [{"away": a, "home": h} for a, h in unique_games]
    print(f"[odds] top-picks span {len(our_games)} game(s)")

    # Fetch today's event list
    next_day = (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat()
    events = _get(f"{BASE}/sports/{SPORT}/events", {
        "apiKey": API_KEY,
        "dateFormat": "iso",
        "commenceTimeFrom": f"{date}T00:00:00Z",
        "commenceTimeTo": f"{next_day}T00:00:00Z",
    })
    if not isinstance(events, list):
        print("[odds] no events list returned; writing nothing.", file=sys.stderr)
        return
    if debug:
        Path("/tmp/odds_events.json").write_text(json.dumps(events, indent=2))
        print(f"[odds] dumped {len(events)} events to /tmp/odds_events.json")

    matched_ids = _match_events(events, our_games)
    if not matched_ids:
        print(f"[odds] matched 0 of {len(events)} events to top-picks games; writing nothing.", file=sys.stderr)
        return
    print(f"[odds] matched {len(matched_ids)}/{len(events)} event(s)")

    all_lines: list[dict] = []
    skipped_points: dict[float, int] = {}
    used_book: str | None = None

    for event_id in matched_ids:
        data = _get(f"{BASE}/sports/{SPORT}/events/{event_id}/odds", {
            "apiKey": API_KEY,
            "regions": "us",
            "markets": HR_MARKET,
            "oddsFormat": "american",
        })
        if not isinstance(data, dict):
            continue
        if debug:
            Path(f"/tmp/odds_event_{event_id}.json").write_text(json.dumps(data, indent=2))
            avail = {mkt.get("key") for bm in data.get("bookmakers") or [] for mkt in bm.get("markets") or []}
            print(f"[odds] event {event_id} available markets: {avail}")

        book_key, outcomes = _pick_book(data.get("bookmakers") or [])
        if not outcomes:
            print(f"[odds] event {event_id}: no {HR_MARKET} market in any bookmaker", file=sys.stderr)
            continue
        used_book = used_book or book_key

        for oc in outcomes:
            # The-Odds-API HR props shape: name="Over"/"Under", description=player,
            # point=the threshold. `point` is NOT always 0.5 — the same market
            # returns 0.5 (anytime HR), 1.5 (2+) and 2.5 (3+) for the same player,
            # and this loop used to drop it and store all three as if they were one
            # line. Everything downstream then compared a 3+ HR price against
            # P(>=1 HR) and reported a 57% edge on a +16000 line.
            if oc.get("name") != "Over":
                continue
            player = str(oc.get("description") or "").strip()
            price = oc.get("price")
            if not player or price is None or _norm(player) not in target_names:
                continue
            point = oc.get("point")
            try:
                point = float(point) if point is not None else None
            except (TypeError, ValueError):
                point = None
            # Only the anytime-HR threshold is comparable to the model's
            # P(at least one HR). A missing point means the book returned a
            # single-threshold market, which for batter_home_runs is anytime.
            if point is not None and abs(point - 0.5) > 1e-6:
                skipped_points[point] = skipped_points.get(point, 0) + 1
                continue
            try:
                all_lines.append({"name": player, "american": int(price),
                                  "point": 0.5, "market": HR_MARKET})
            except (TypeError, ValueError):
                continue

    if skipped_points:
        print(f"[odds] skipped non-anytime thresholds: "
              f"{', '.join(f'{k}+: {v}' for k, v in sorted(skipped_points.items()))}")

    if not all_lines:
        print("[odds] 0 matching lines for top picks; writing nothing.", file=sys.stderr)
        return

    name_idx = _name_to_id()
    for ln in all_lines:
        ln["batter_id"] = name_idx.get(_norm(ln["name"]))

    # One line per batter. If a book still returns several anytime prices, keep
    # the shortest (most probable) — never let an arbitrary iteration order pick.
    best: dict = {}
    for ln in all_lines:
        key = ln.get("batter_id") or _norm(ln["name"])
        if key not in best or ln["american"] < best[key]["american"]:
            best[key] = ln
    if len(best) < len(all_lines):
        print(f"[odds] deduped {len(all_lines)} lines -> {len(best)} batters")
    all_lines = list(best.values())

    out_path = DATA_DIR / f"odds_{date}.json"
    out_path.write_text(json.dumps({
        "date": date,
        "book": used_book or "unknown",
        "pulled_at": dt.datetime.utcnow().isoformat() + "Z",
        "lines": all_lines,
    }, indent=2))
    matched = sum(1 for ln in all_lines if ln.get("batter_id"))
    print(f"[odds] wrote {out_path.name}: {len(all_lines)} lines, {matched} batter_ids resolved, book={used_book}")


if __name__ == "__main__":
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    debug_flag = "--debug" in sys.argv
    top_n_arg = DEFAULT_TOP_N
    for a in sys.argv[1:]:
        if a.startswith("--top="):
            try:
                top_n_arg = int(a.split("=", 1)[1])
            except ValueError:
                pass
    date_arg = pos[0] if pos else dt.date.today().isoformat()
    try:
        main(date_arg, debug=debug_flag, top_n=top_n_arg)
    except Exception as e:
        print(f"[odds] unhandled error (pipeline continues): {e}", file=sys.stderr)
        sys.exit(0)
