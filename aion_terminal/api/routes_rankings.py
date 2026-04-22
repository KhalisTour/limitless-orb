from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from aion_terminal.features.contracts import score_and_rank_contracts
from aion_terminal.features.dealer import compute_levels
from aion_terminal.features.technical import build_technical_features
from aion_terminal.models.dto import ManualNarrativeTagRecord, UnderlyingBarRecord
from aion_terminal.services.ranking_service import rank_symbol, rank_universe
from aion_terminal.signals.setups import evaluate_symbol_snapshot
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.storage.repositories import query_latest_chain, upsert_manual_narrative_tags
from aion_terminal.app.config import settings
from aion_terminal.utils.math_utils import as_float

router = APIRouter(tags=["rankings"])
SCHEMA_PATH = "aion_terminal/storage/schema.sql"


@router.get("/rankings/health")
def rankings_health():
    return {"ok": True}


def _validate_bias(bias: str | None) -> str | None:
    if bias is None:
        return None
    normalized = bias.lower()
    if normalized not in {"bullish", "bearish"}:
        raise HTTPException(status_code=422, detail="bias must be bullish or bearish")
    return normalized


@router.get("/rankings")
def get_rankings(
    setup_class: str | None = None,
    bias: str | None = None,
    dte_min: int = 9,
    dte_max: int = 14,
    limit: int = 20,
    min_confidence: float = 0.3,
):
    selected_bias = _validate_bias(bias)
    rankings = rank_universe(dte_min=dte_min, dte_max=dte_max, min_confidence=min_confidence)

    filtered = rankings
    if setup_class:
        filtered = [
            r
            for r in filtered
            if any(str(sig.get("setup_class")) == setup_class for sig in r.signals)
        ]
    if selected_bias:
        filtered = [
            r for r in filtered if any(str(sig.get("bias")) == selected_bias for sig in r.signals)
        ]

    return [asdict(r) for r in filtered[: max(1, limit)]]


@router.get("/rankings/{symbol}")
def get_ranking_symbol(
    symbol: str,
    bias: str | None = None,
    dte_min: int = 9,
    dte_max: int = 14,
    budget: float | None = None,
    min_confidence: float = 0.3,
):
    selected_bias = _validate_bias(bias)
    ranking = rank_symbol(
        symbol.upper(),
        dte_min=dte_min,
        dte_max=dte_max,
        budget=budget,
        min_confidence=min_confidence,
    )
    if not ranking.errors and ranking.spot == 0.0:
        raise HTTPException(status_code=404, detail=f"No chain data for {symbol.upper()}")

    payload = asdict(ranking)
    if selected_bias:
        payload["signals"] = [s for s in payload["signals"] if s.get("bias") == selected_bias]
    return payload


@router.get("/setups/{symbol}")
def get_setups_for_symbol(symbol: str):
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        chain = query_latest_chain(conn, symbol.upper())
        if not chain:
            raise HTTPException(status_code=404, detail=f"No chain data for {symbol.upper()}")

        spot = as_float(chain[0].get("underlying_price"))
        dealer = compute_levels(chain, spot=spot, symbol=symbol.upper())

        rows = conn.execute(
            """
            SELECT symbol, timeframe, bar_ts, open, high, low, close, volume, vwap
            FROM underlying_bars
            WHERE symbol = ?
            ORDER BY bar_ts DESC
            LIMIT 60
            """,
            (symbol.upper(),),
        ).fetchall()
        bars = [
            UnderlyingBarRecord(
                symbol=r["symbol"],
                timeframe=r["timeframe"],
                bar_ts=r["bar_ts"],
                open=as_float(r["open"]),
                high=as_float(r["high"]),
                low=as_float(r["low"]),
                close=as_float(r["close"]),
                volume=r["volume"],
                vwap=as_float(r["vwap"]),
            )
            for r in reversed(rows)
        ]
        features, state = build_technical_features(bars, "D")
        signals = evaluate_symbol_snapshot(symbol.upper(), dealer, features, state, [], min_confidence=0.3)

        serialized = []
        from aion_terminal.services.ranking_service import serialize_signal

        serialized = [serialize_signal(s) for s in signals]

        return {
            "symbol": symbol.upper(),
            "signals": serialized,
            "spot": spot,
            "regime": dealer.get("regime"),
            "king_node": dealer.get("king_node"),
        }
    finally:
        conn.close()


@router.get("/contracts/{symbol}")
def get_contracts_for_symbol(
    symbol: str,
    bias: str = "bearish",
    dte_min: int = 9,
    dte_max: int = 14,
    budget: float | None = None,
):
    selected_bias = _validate_bias(bias) or "bearish"

    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        chain = query_latest_chain(conn, symbol.upper())
        if not chain:
            raise HTTPException(status_code=404, detail=f"No chain data for {symbol.upper()}")
        spot = as_float(chain[0].get("underlying_price"))
        rec = score_and_rank_contracts(
            conn,
            symbol.upper(),
            selected_bias,
            spot,
            dte_min=dte_min,
            dte_max=dte_max,
            budget=budget,
        )
        return {
            "symbol": symbol.upper(),
            "bias": selected_bias,
            "spot": spot,
            "best": asdict(rec.best) if rec.best else None,
            "safer": asdict(rec.safer) if rec.safer else None,
            "convex": asdict(rec.convex) if rec.convex else None,
            "all_scored": [asdict(c) for c in rec.all_scored[:20]],
            "warnings": rec.warnings,
        }
    finally:
        conn.close()


@router.post("/tags/manual")
def post_manual_tag(payload: dict[str, Any] = Body(...)):
    symbol = str(payload.get("symbol", "")).upper().strip()
    tag_key = str(payload.get("tag_key", "")).strip()
    tag_value = payload.get("tag_value")
    tag_date = payload.get("tag_date")

    if not symbol or not tag_key:
        raise HTTPException(status_code=422, detail="symbol and tag_key are required")

    if not tag_date:
        tag_date = datetime.now(timezone.utc).date().isoformat()

    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        upsert_manual_narrative_tags(
            conn,
            [
                ManualNarrativeTagRecord(
                    symbol=symbol,
                    tag_date=str(tag_date),
                    tag_key=tag_key,
                    tag_value=tag_value,
                    context_json=payload.get("note"),
                )
            ],
        )
        return {"ok": True, "symbol": symbol, "tag_key": tag_key, "tag_date": str(tag_date)}
    finally:
        conn.close()
