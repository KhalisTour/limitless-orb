from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import WebSocket

from aion_terminal.app.config import settings
from aion_terminal.services.alerts_service import check_alerts, dispatch_alerts
from aion_terminal.services.ingestion_service import ingest_symbol
from aion_terminal.services.snapshot_service import ensure_levels_payload
from aion_terminal.storage import repositories
from aion_terminal.storage.db import bootstrap_schema, get_connection

logger = logging.getLogger(__name__)


def levels_without_curve(levels: dict[str, Any]) -> dict[str, Any]:
    combined = levels.get("combined_levels") or levels
    return {k: v for k, v in combined.items() if k not in {"curve", "acceleration_zones"}}


def _get_conn():
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    return conn


def _attach_curve_expiry_labels(levels: dict[str, Any]) -> dict[str, Any]:
    combined = levels.get("combined_levels") or levels
    expiry_levels = levels.get("expiry_levels") or {}

    curve = []
    for point in combined.get("curve", []):
        strike = point.get("strike")
        chosen_expiry = "combined"
        for expiry, block in expiry_levels.items():
            if any(p.get("strike") == strike for p in block.get("curve", [])):
                chosen_expiry = expiry
                break
        curve.append({"strike": strike, "net_exposure": point.get("net_exposure"), "expiry": chosen_expiry})

    out = dict(levels)
    out["curve"] = curve
    out.update({k: v for k, v in combined.items() if k != "curve"})
    return out


def compute_or_load_levels(ticker: str, runtime_state, expiry_override: str | None = None) -> dict[str, Any]:
    if ticker in runtime_state.latest_levels:
        cached = runtime_state.latest_levels[ticker]
        if expiry_override and expiry_override != "combined":
            expiry_levels = cached.get("expiry_levels") or {}
            if expiry_override in expiry_levels:
                chosen = expiry_levels[expiry_override]
                return {"symbol": ticker, "spot": chosen.get("spot"), "curve": chosen.get("curve", [])}
        if not expiry_override or expiry_override == "combined":
            return _attach_curve_expiry_labels(cached)

    if settings.cache_only:
        conn = _get_conn()
        try:
            rows = repositories.query_latest_chain(conn, ticker)
            if not rows:
                return {"symbol": ticker, "spot": 0.0, "curve": [], "warnings": ["cache_miss", "refresh_required"], "cache_only": True}
            from aion_terminal.features.dealer import compute_levels
            spot = rows[0].get("underlying_price") or 0.0
            levels = compute_levels(rows, spot=spot, symbol=ticker)
            levels["cache_only"] = True
            return levels
        finally:
            conn.close()

    conn = _get_conn()
    try:
        levels = ingest_symbol(
            conn,
            symbol=ticker,
            token=settings.marketdata_token,
            api_url_template=settings.api_url_template,
            dte_max=settings.dte_max,
        )
        levels = ensure_levels_payload(levels, conn=conn, symbol=ticker, spot=levels.get("spot", 0.0))
        runtime_state.latest_levels[ticker] = levels

        if expiry_override and expiry_override != "combined":
            expiry_levels = levels.get("expiry_levels") or {}
            if expiry_override in expiry_levels:
                chosen = expiry_levels[expiry_override]
                return {"symbol": ticker, "spot": chosen.get("spot"), "curve": chosen.get("curve", [])}
            logger.warning("expiry-specific curve requested but missing; fallback to compatibility rows symbol=%s expiry=%s", ticker, expiry_override)
            rows = repositories.query_latest_chain(conn, ticker)
            filtered = [r for r in rows if r.get("expiry") == expiry_override]
            if filtered:
                from aion_terminal.features.dealer import compute_levels

                fallback = compute_levels(filtered, levels.get("spot", 0.0), symbol=ticker)
                return {"symbol": ticker, "spot": fallback.get("spot"), "curve": fallback.get("curve", [])}
            return {"symbol": ticker, "spot": levels.get("spot"), "curve": []}

        return _attach_curve_expiry_labels(levels)
    finally:
        conn.close()


def query_expiries(symbol: str, runtime_state=None) -> list[str]:
    if runtime_state and symbol in runtime_state.latest_levels:
        expiry_levels = runtime_state.latest_levels[symbol].get("expiry_levels") or {}
        if expiry_levels:
            return sorted(expiry_levels.keys())

    conn = _get_conn()
    try:
        return repositories.query_expiries(conn, symbol, dte_max=settings.dte_max)
    finally:
        conn.close()


async def broadcast_payload(runtime_state, payload: dict[str, Any]) -> None:
    failed = []
    for connection in list(runtime_state.active_connections):
        try:
            await connection.send_json(payload)
        except Exception:
            failed.append(connection)
    for conn in failed:
        if conn in runtime_state.active_connections:
            runtime_state.active_connections.remove(conn)


def pipeline_loop(runtime_state) -> None:
    if settings.cache_only or not settings.marketdata_enabled:
        logger.info(
            "background ingestion pipeline disabled cache_only=%s marketdata_enabled=%s",
            settings.cache_only,
            settings.marketdata_enabled,
        )
        return

    while True:
        for ticker in settings.watchlist:
            conn = _get_conn()
            try:
                levels = ingest_symbol(
                    conn,
                    symbol=ticker,
                    token=settings.marketdata_token,
                    api_url_template=settings.api_url_template,
                    dte_max=settings.dte_max,
                )
                levels = ensure_levels_payload(levels, conn=conn, symbol=ticker, spot=levels.get("spot", 0.0))
                combined = levels.get("combined_levels") or levels

                previous = repositories.query_previous_levels(conn, ticker)
                alerts = check_alerts(combined, previous)
                dispatch_alerts(alerts)
                runtime_state.latest_levels[ticker] = levels
                asyncio.run(
                    broadcast_payload(
                        runtime_state,
                        {"type": "update", "symbol": ticker, "data": levels_without_curve(levels)},
                    )
                )
            except Exception as exc:  # pragma: no cover
                logger.exception("Pipeline error for %s: %s", ticker, exc)
            finally:
                conn.close()
        time.sleep(settings.poll_seconds)


async def websocket_register(websocket: WebSocket, ticker: str, runtime_state) -> None:
    await websocket.accept()
    runtime_state.active_connections.append(websocket)
    levels = compute_or_load_levels(ticker, runtime_state)
    await websocket.send_json(levels_without_curve(levels))
