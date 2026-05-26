"""User trade logger routes."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel

from aion_terminal.app.config import settings
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.utils.time_utils import utc_now_iso

router = APIRouter(tags=["trades"])
SCHEMA_PATH = "aion_terminal/storage/schema.sql"


class UserTradeRequest(BaseModel):
    symbol: str
    direction: str
    trade_date: str | None = None
    contract_symbol: str | None = None
    side: str | None = None
    strike: float | None = None
    expiry: str | None = None
    dte_at_entry: int | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    contracts: int | None = None
    exit_reason: str | None = None
    setup_source: str = "manual"
    notes: str | None = None


def _compute_pnl(direction: str, entry: float | None, exit_: float | None, contracts: int | None):
    if entry is None or exit_ is None or contracts is None:
        return None, None
    multiplier = 100  # options contracts
    if direction == "short":
        pnl_dollars = (entry - exit_) * contracts * multiplier
    else:
        pnl_dollars = (exit_ - entry) * contracts * multiplier
    pnl_pct = ((exit_ - entry) / entry * 100.0) if entry else None
    if direction == "short" and pnl_pct is not None:
        pnl_pct = -pnl_pct
    return pnl_dollars, pnl_pct


@router.post("/trades/log")
async def log_user_trade(body: UserTradeRequest):
    trade_id = str(uuid4())
    trade_date = body.trade_date or date.today().isoformat()
    direction = (body.direction or "long").lower()
    pnl_dollars, pnl_pct = _compute_pnl(direction, body.entry_price, body.exit_price, body.contracts)
    now = utc_now_iso()

    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        conn.execute(
            """
            INSERT INTO user_trades (
                trade_id, logged_at, trade_date, symbol, direction, contract_symbol,
                side, strike, expiry, dte_at_entry, entry_price, exit_price, contracts,
                pnl_dollars, pnl_pct, exit_reason, setup_source, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id, now, trade_date, body.symbol.upper(), direction,
                body.contract_symbol, body.side, body.strike, body.expiry,
                body.dte_at_entry, body.entry_price, body.exit_price, body.contracts,
                pnl_dollars, pnl_pct, body.exit_reason, body.setup_source, body.notes,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"trade_id": trade_id, "status": "logged", "pnl_dollars": pnl_dollars, "pnl_pct": pnl_pct}


def _row_to_dict(r) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


@router.get("/trades/history")
async def get_trade_history(symbol: str | None = None, days: int = 30):
    days = max(1, min(int(days), 3650))
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        if symbol:
            rows = conn.execute(
                """
                SELECT * FROM user_trades
                WHERE symbol = ? AND trade_date >= date('now', ?)
                ORDER BY trade_date DESC, logged_at DESC
                """,
                (symbol.upper(), f"-{days} days"),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM user_trades
                WHERE trade_date >= date('now', ?)
                ORDER BY trade_date DESC, logged_at DESC
                """,
                (f"-{days} days",),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


@router.get("/trades/summary")
async def get_trade_summary(days: int = 30):
    days = max(1, min(int(days), 3650))
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        rows = conn.execute(
            "SELECT * FROM user_trades WHERE trade_date >= date('now', ?)",
            (f"-{days} days",),
        ).fetchall()
    finally:
        conn.close()

    trades = [_row_to_dict(r) for r in rows]
    total = len(trades)

    def is_winner(t: dict[str, Any]) -> bool | None:
        ep = t.get("entry_price")
        xp = t.get("exit_price")
        if ep is None or xp is None:
            return None
        if (t.get("direction") or "long") == "short":
            return xp < ep
        return xp > ep

    closed = [t for t in trades if is_winner(t) is not None]
    wins = [t for t in closed if is_winner(t)]
    win_rate = (len(wins) / len(closed)) if closed else 0.0

    pnl_pcts = [float(t["pnl_pct"]) for t in trades if t.get("pnl_pct") is not None]
    avg_pnl_pct = (sum(pnl_pcts) / len(pnl_pcts)) if pnl_pcts else 0.0
    best = max(pnl_pcts) if pnl_pcts else None
    worst = min(pnl_pcts) if pnl_pcts else None

    by_symbol: dict[str, dict[str, Any]] = {}
    by_source: dict[str, dict[str, Any]] = {}
    for t in trades:
        sym = t.get("symbol") or "UNKNOWN"
        src = t.get("setup_source") or "unknown"
        for bucket, key in ((by_symbol, sym), (by_source, src)):
            entry = bucket.setdefault(key, {"count": 0, "wins": 0, "pnl_pct_sum": 0.0, "pnl_pct_n": 0})
            entry["count"] += 1
            w = is_winner(t)
            if w is True:
                entry["wins"] += 1
            if t.get("pnl_pct") is not None:
                entry["pnl_pct_sum"] += float(t["pnl_pct"])
                entry["pnl_pct_n"] += 1

    def finalize(bucket: dict[str, dict[str, Any]]):
        out = {}
        for k, v in bucket.items():
            avg = (v["pnl_pct_sum"] / v["pnl_pct_n"]) if v["pnl_pct_n"] else 0.0
            out[k] = {"count": v["count"], "wins": v["wins"], "avg_pnl_pct": avg}
        return out

    return {
        "total_trades": total,
        "closed_trades": len(closed),
        "win_rate": win_rate,
        "avg_pnl_pct": avg_pnl_pct,
        "best_pnl_pct": best,
        "worst_pnl_pct": worst,
        "by_symbol": finalize(by_symbol),
        "by_setup_source": finalize(by_source),
    }
