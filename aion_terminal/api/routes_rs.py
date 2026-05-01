from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import threading
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from aion_terminal.data.rs_engine import SCAN_INTERVAL_DAYS, run_rs_scan
from aion_terminal.signals.leadership import detect_leadership_events
from aion_terminal.storage.rs_repositories import (
    bootstrap_rs_schema,
    get_rs_connection,
    log_scan_run,
    mark_promoted,
    query_candidate_by_ticker,
    query_passing_candidates,
    query_recent_scan_runs,
    upsert_rs_candidate,
)

router = APIRouter(tags=["rs"])
_scan_lock = threading.Lock()


@router.get("/rs/candidates")
def get_rs_candidates(
    min_percentile: float = 75.0,
    min_conditions: int = 3,
    limit: int = 50,
):
    conn = get_rs_connection()
    bootstrap_rs_schema(conn)
    try:
        return query_passing_candidates(
            conn,
            min_percentile=min_percentile,
            min_conditions=min_conditions,
            limit=limit,
        )
    finally:
        conn.close()


@router.get("/rs/candidates/{ticker}")
def get_rs_candidate(ticker: str):
    conn = get_rs_connection()
    bootstrap_rs_schema(conn)
    try:
        candidate = query_candidate_by_ticker(conn, ticker)
        if not candidate:
            raise HTTPException(status_code=404, detail=f"No RS candidate for {ticker.upper()}")
        return candidate
    finally:
        conn.close()


@router.get("/rs/scan/status")
def get_rs_scan_status():
    conn = get_rs_connection()
    bootstrap_rs_schema(conn)
    try:
        recent = query_recent_scan_runs(conn, limit=1)
        last_run = recent[0]["run_at"] if recent else None

        next_run_after = None
        if last_run:
            try:
                dt = datetime.fromisoformat(last_run)
                next_run_after = (dt + timedelta(days=SCAN_INTERVAL_DAYS)).isoformat()
            except ValueError:
                next_run_after = None

        total = conn.execute("SELECT COUNT(1) AS c FROM rs_candidates").fetchone()["c"]
        passing = conn.execute(
            "SELECT COUNT(1) AS c FROM rs_candidates WHERE passes_screen = 1"
        ).fetchone()["c"]
        top_5 = conn.execute(
            """
            SELECT ticker, rs_percentile, conditions_met, close
            FROM rs_candidates
            WHERE passes_screen = 1
            ORDER BY rs_percentile DESC
            LIMIT 5
            """
        ).fetchall()

        return {
            "last_run": last_run,
            "next_run_after": next_run_after,
            "candidates_total": int(total),
            "candidates_passing": int(passing),
            "top_5": [dict(row) for row in top_5],
        }
    finally:
        conn.close()


@router.post("/rs/promote/{ticker}")
def post_promote_rs_ticker(ticker: str, payload: dict[str, Any] = Body(default={})):
    add_to_watchlist = bool(payload.get("add_to_watchlist", False))

    conn = get_rs_connection()
    bootstrap_rs_schema(conn)
    try:
        candidate = query_candidate_by_ticker(conn, ticker)
        if not candidate:
            raise HTTPException(status_code=404, detail=f"No RS candidate for {ticker.upper()}")
        mark_promoted(conn, ticker, promoted=True)
    finally:
        conn.close()

    watchlist_note = ""
    if add_to_watchlist:
        watchlist_note = (
            "Set add_to_watchlist=true requested. Update WATCHLIST env var manually to include "
            f"{ticker.upper()}."
        )

    return {"ok": True, "ticker": ticker.upper(), "promoted": True, "watchlist_note": watchlist_note}


def _run_scan_job(force: bool = False) -> None:
    acquired = _scan_lock.acquire(blocking=False)
    if not acquired:
        return

    try:
        conn = get_rs_connection()
        bootstrap_rs_schema(conn)
        started = datetime.now()
        try:
            if not force:
                recent = query_recent_scan_runs(conn, limit=1)
                if recent:
                    last = recent[0].get("run_at")
                    if last:
                        try:
                            last_dt = datetime.fromisoformat(last)
                            if datetime.now(last_dt.tzinfo) - last_dt < timedelta(days=SCAN_INTERVAL_DAYS):
                                return
                        except ValueError:
                            pass
            features = run_rs_scan()
            for feat in features:
                upsert_rs_candidate(conn, feat)
            elapsed = (datetime.now() - started).total_seconds()
            log_scan_run(
                conn,
                tickers_scanned=0,
                tickers_passing=len(features),
                duration_seconds=elapsed,
                errors="",
            )
        except Exception as exc:
            elapsed = (datetime.now() - started).total_seconds()
            log_scan_run(conn, 0, 0, elapsed, errors=str(exc))
        finally:
            conn.close()
    finally:
        _scan_lock.release()


@router.post("/rs/scan/trigger")
def post_trigger_rs_scan(payload: dict[str, Any] = Body(default={})):
    force = bool(payload.get("force", False))
    if _scan_lock.locked():
        return {"ok": False, "message": "RS scan already in progress"}

    t = threading.Thread(target=_run_scan_job, kwargs={"force": force}, daemon=True)
    t.start()
    return {"ok": True, "message": "RS scan launched in background"}


@router.get("/rs/history")
def get_rs_history(limit: int = 30):
    """Return recent RS scan runs with the current top tickers as a snapshot."""
    limit = max(1, min(limit, 100))
    conn = get_rs_connection()
    bootstrap_rs_schema(conn)
    try:
        runs = query_recent_scan_runs(conn, limit=limit)
        top = conn.execute(
            """
            SELECT ticker, rs_score, rs_percentile, conditions_met,
                   rs_new_high_before_price
            FROM rs_candidates
            WHERE passes_screen = 1
            ORDER BY rs_score DESC
            LIMIT 5
            """
        ).fetchall()
        rs_before_count = conn.execute(
            "SELECT COUNT(1) AS c FROM rs_candidates WHERE rs_new_high_before_price = 1"
        ).fetchone()["c"]
    finally:
        conn.close()

    top_tickers = [dict(t) for t in top]
    result = []
    for row in runs:
        result.append(
            {
                "scan_ts": row.get("run_at"),
                "tickers_scanned": row.get("tickers_scanned", 0),
                "total_passing": row.get("tickers_passing", 0),
                "duration_seconds": row.get("duration_seconds", 0.0),
                "rs_before_price_count": int(rs_before_count or 0),
                "top_tickers": top_tickers,
            }
        )
    return result


@router.get("/rs/events")
def get_rs_events(min_percentile: float = 75.0):
    conn = get_rs_connection()
    bootstrap_rs_schema(conn)
    try:
        candidates = query_passing_candidates(
            conn,
            min_percentile=0.0,
            min_conditions=0,
            include_expired=True,
            limit=1000,
        )
    finally:
        conn.close()

    features = []
    for c in candidates:
        # Imported lazily to avoid circular assumptions with API-only usage.
        from aion_terminal.data.rs_engine import RSFeatures

        features.append(
            RSFeatures(
                ticker=str(c.get("ticker", "")),
                computed_at=str(c.get("updated_at", "")),
                rs_score=float(c.get("rs_score", 0.0)),
                rs_percentile=float(c.get("rs_percentile", 0.0)),
                rs_new_high_63d=bool(c.get("rs_new_high_63d", 0)),
                rs_new_high_before_price=bool(c.get("rs_new_high_before_price", 0)),
                price_above_200d_ma=bool(c.get("price_above_200d_ma", 0)),
                price_above_50w_ma=bool(c.get("price_above_50w_ma", 0)),
                return_1y_pct=float(c.get("return_1y_pct", 0.0)),
                close=float(c.get("close", 0.0)),
                conditions_met=int(c.get("conditions_met", 0)),
                passes_screen=bool(c.get("passes_screen", 0)),
            )
        )

    events = detect_leadership_events(features, min_percentile=min_percentile)
    return [asdict(e) for e in events]


# main.py wiring note:
#   from aion_terminal.api import routes_rs
#   app.include_router(routes_rs.router)
