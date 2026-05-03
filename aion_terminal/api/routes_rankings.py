from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from aion_terminal.features.contracts import score_and_rank_contracts
from aion_terminal.features.dealer import compute_levels
from aion_terminal.features.technical import build_technical_features
from aion_terminal.models.dto import ManualNarrativeTagRecord, UnderlyingBarRecord
from aion_terminal.services.ranking_service import get_rankings_unified, infer_bias, rank_symbol, rank_universe
from aion_terminal.signals.setups import evaluate_symbol_snapshot
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.storage.repositories import query_latest_chain, upsert_manual_narrative_tags
from aion_terminal.app.config import settings
from aion_terminal.utils.math_utils import as_float
from aion_terminal.utils.time_utils import utc_now_iso

router = APIRouter(tags=["rankings"])
SCHEMA_PATH = "aion_terminal/storage/schema.sql"


def _cache_only_payload(symbol: str, data_key: str, data_value):
    return {
        "symbol": symbol.upper(),
        data_key: data_value,
        "warnings": ["cache_miss", "refresh_required"],
        "cache_only": True,
    }


def _route_refresh_allowed(refresh: bool) -> bool:
    return refresh and settings.allow_route_refresh and (settings.marketdata_enabled or not settings.cache_only)


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
    dte_min: int = 0,
    dte_max: int = 21,
    limit: int = 10,
    min_confidence: float = 0.3,
    symbol: str | None = None,
):
    """Get unified ranked candidates with automatic degradation.
    
    Query params:
      - setup_class: filter by setup class (e.g., 'multi_level_breakout')
      - bias: filter by bias ('bullish' or 'bearish')
      - dte_min, dte_max: contract expiry window
      - limit: max items to return (default 10)
      - min_confidence: minimum confidence threshold for strict ranking
      - symbol: filter by single symbol (optional)
    
    Returns: list of RankedItem with unified schema + warnings.
    If strict thresholds yield too few candidates, provides degraded list.
    """
    selected_bias = _validate_bias(bias)
    symbols = [symbol.upper()] if symbol else None
    
    items, warnings = get_rankings_unified(
        symbols=symbols,
        setup_class=setup_class,
        bias=selected_bias,
        limit=limit,
        dte_min=dte_min,
        dte_max=dte_max,
        min_confidence=min_confidence,
    )
    
    result = {
        "rankings": [asdict(item) for item in items],
        "warnings": warnings,
        "generated_at": utc_now_iso(),
        "cache_only": settings.cache_only,
    }
    return result


@router.get("/rankings/{symbol}")
def get_ranking_symbol(
    symbol: str,
    bias: str | None = None,
    dte_min: int = 0,
    dte_max: int = 21,
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
    if not ranking.errors and ranking.spot == 0.0 and not settings.cache_only:
        raise HTTPException(status_code=404, detail=f"No chain data for {symbol.upper()}")

    payload = asdict(ranking)
    if selected_bias:
        payload["signals"] = [s for s in payload["signals"] if s.get("bias") == selected_bias]
    payload["cache_only"] = settings.cache_only
    if settings.cache_only and (not payload.get("signals")) and payload.get("spot", 0.0) == 0.0:
        payload.setdefault("warnings", ["cache_miss", "refresh_required"])
    return payload


@router.get("/setups/{symbol}")
def get_setups_for_symbol(symbol: str):
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        chain = query_latest_chain(conn, symbol.upper())
        if not chain:
            if settings.cache_only:
                return _cache_only_payload(symbol, "signals", [])
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
            "cache_only": settings.cache_only,
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
    bias: str | None = None,
    dte_min: int = 0,
    dte_max: int = 21,
    budget: float | None = None,
    refresh: bool = False,
):
    selected_bias = _validate_bias(bias)
    if refresh and not _route_refresh_allowed(refresh):
        return {
            "symbol": symbol.upper(),
            "bias": selected_bias,
            "spot": 0.0,
            "best": None,
            "safer": None,
            "convex": None,
            "all_scored": [],
            "warnings": ["route_refresh_disabled"],
            "cache_only": settings.cache_only,
        }

    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    try:
        chain = query_latest_chain(conn, symbol.upper())
        if not chain:
            if settings.cache_only:
                return {
                    "symbol": symbol.upper(),
                    "bias": selected_bias,
                    "spot": 0.0,
                    "best": None,
                    "safer": None,
                    "convex": None,
                    "all_scored": [],
                    "warnings": ["cache_miss", "refresh_required"],
                    "cache_only": True,
                }
            raise HTTPException(status_code=404, detail=f"No chain data for {symbol.upper()}")
        spot = as_float(chain[0].get("underlying_price"))
        dealer = compute_levels(chain, spot=spot, symbol=symbol.upper())
        rows = conn.execute(
            """SELECT symbol, timeframe, bar_ts, open, high, low, close, volume, vwap FROM underlying_bars WHERE symbol = ? ORDER BY bar_ts DESC LIMIT 60""",
            (symbol.upper(),),
        ).fetchall()
        bars = [UnderlyingBarRecord(symbol=r["symbol"], timeframe=r["timeframe"], bar_ts=r["bar_ts"], open=as_float(r["open"]), high=as_float(r["high"]), low=as_float(r["low"]), close=as_float(r["close"]), volume=r["volume"], vwap=as_float(r["vwap"])) for r in reversed(rows)]
        _, technical_state = build_technical_features(bars, "D")
        inferred_bias, bias_warnings = infer_bias(explicit_bias=selected_bias, technical_state=technical_state, dealer_features=dealer)
        rec = score_and_rank_contracts(
            conn,
            symbol.upper(),
            inferred_bias,
            spot,
            dte_min=dte_min,
            dte_max=dte_max,
            budget=budget,
        )
        return {
            "cache_only": settings.cache_only,
            "symbol": symbol.upper(),
            "bias": inferred_bias,
            "spot": spot,
            "best": asdict(rec.best) if rec.best else None,
            "safer": asdict(rec.safer) if rec.safer else None,
            "convex": asdict(rec.convex) if rec.convex else None,
            "all_scored": [asdict(c) for c in rec.all_scored[:20]],
            "warnings": [*rec.warnings, *bias_warnings],
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
