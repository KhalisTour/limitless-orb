"""
Phase 5b — pull free HR-prop odds and write data/odds_<date>.json.

Source: DraftKings' public sportsbook offering JSON (no API key, no login).
We read the MLB event group, find the "home runs" prop subcategory, and
record each hitter's American odds "to hit a home run". Names are matched to
batter_id via the latest daily snapshot so the API can join odds to the model.

IMPORTANT (read before relying on this):
  * DraftKings endpoints are geo-fenced and bot-protected. From datacenter
    IPs (Railway / GitHub Actions) this frequently returns 403. This script
    therefore NEVER raises on failure — it logs, writes nothing, and exits 0
    so the daily pipeline keeps going. The API serves `available:false` when
    no odds file exists.
  * The exact category/subcategory naming and offer shape can change. The
    parser below is intentionally permissive and has a --debug mode that dumps
    the raw structure so the first live run can be verified/adjusted.

Usage:
  python fetch_odds.py [YYYY-MM-DD] [--debug]
"""

from __future__ import annotations

import json
import sys
import datetime as dt
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
SNAP_DIR = DATA_DIR / "snapshots"

MLB_EVENT_GROUP = 84240  # DraftKings MLB
BASE = "https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups"
HEADERS = {
    # A realistic UA reduces (does not eliminate) bot blocking.
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT = 20


def _get(url: str) -> dict | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[odds] {url} -> HTTP {r.status_code} (likely geo/bot block)", file=sys.stderr)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001 — never fail the pipeline
        print(f"[odds] request failed: {e}", file=sys.stderr)
        return None


def _name_to_id_map(date: str) -> dict[str, str]:
    """Latest snapshot batters_*.csv -> {normalized 'first last': player_id}."""
    idx: dict[str, str] = {}
    if not SNAP_DIR.exists():
        return idx
    import csv

    days = sorted([d for d in SNAP_DIR.iterdir() if d.is_dir()], reverse=True)
    for d in days:
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
                        full = f"{parts[1]} {parts[0]}".lower()
                        idx.setdefault(full, str(pid).split(".")[0])
            except Exception:  # noqa: BLE001
                continue
        if idx:
            break
    return idx


def _find_hr_subcategory(group: dict) -> tuple[int, int] | None:
    """Return (category_id, subcategory_id) for the home-run prop market."""
    eg = group.get("eventGroup") or {}
    for cat in eg.get("offerCategories") or []:
        for desc in cat.get("offerSubcategoryDescriptors") or []:
            name = (desc.get("name") or "").lower()
            if "home run" in name and "first" not in name and "last" not in name:
                return cat.get("offerCategoryId"), desc.get("subcategoryId")
    return None


def _parse_offers(sub_group: dict) -> list[dict]:
    """Walk the subcategory offering and pull (name, american) for HR=Yes."""
    lines: list[dict] = []
    eg = sub_group.get("eventGroup") or {}
    for cat in eg.get("offerCategories") or []:
        for desc in cat.get("offerSubcategoryDescriptors") or []:
            sub = desc.get("offerSubcategory") or {}
            for offer_set in sub.get("offers") or []:
                for offer in offer_set or []:
                    for oc in offer.get("outcomes") or []:
                        label = (oc.get("label") or "").strip()
                        american = oc.get("oddsAmerican")
                        # "to hit a HR" markets list the player as the participant
                        # or label; skip the "No" side.
                        if label.lower() in ("no", "under"):
                            continue
                        player = oc.get("participant") or (
                            label if label.lower() not in ("yes", "over") else offer.get("label")
                        )
                        if not player or american is None:
                            continue
                        try:
                            american = int(str(american).replace("−", "-"))
                        except ValueError:
                            continue
                        lines.append({"name": str(player).strip(), "american": american})
    # dedupe by name, keep first
    seen: set[str] = set()
    deduped: list[dict] = []
    for ln in lines:
        key = ln["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ln)
    return deduped


def main(date: str, debug: bool = False) -> None:
    group = _get(f"{BASE}/{MLB_EVENT_GROUP}?format=json")
    if not group:
        print("[odds] no event group; writing nothing.", file=sys.stderr)
        return
    if debug:
        Path("/tmp/dk_eventgroup.json").write_text(json.dumps(group, indent=2))
        print("[odds] dumped event group to /tmp/dk_eventgroup.json", file=sys.stderr)

    found = _find_hr_subcategory(group)
    if not found:
        print("[odds] no home-run subcategory found.", file=sys.stderr)
        return
    cat_id, sub_id = found
    sub = _get(f"{BASE}/{MLB_EVENT_GROUP}/categories/{cat_id}/subcategories/{sub_id}?format=json")
    if not sub:
        return

    lines = _parse_offers(sub)
    if not lines:
        print("[odds] parsed 0 lines (shape may have changed; run --debug).", file=sys.stderr)
        return

    name_idx = _name_to_id_map(date)
    for ln in lines:
        ln["batter_id"] = name_idx.get(ln["name"].lower())

    out = {
        "date": date,
        "book": "draftkings",
        "pulled_at": dt.datetime.utcnow().isoformat() + "Z",
        "lines": lines,
    }
    out_path = DATA_DIR / f"odds_{date}.json"
    out_path.write_text(json.dumps(out, indent=2))
    matched = sum(1 for ln in lines if ln.get("batter_id"))
    print(f"[odds] wrote {out_path.name}: {len(lines)} lines, {matched} matched to batter_id")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    debug_flag = "--debug" in sys.argv
    date_arg = args[0] if args else dt.date.today().isoformat()
    try:
        main(date_arg, debug=debug_flag)
    except Exception as e:  # noqa: BLE001 — never break the pipeline
        print(f"[odds] unexpected error (ignored): {e}", file=sys.stderr)
