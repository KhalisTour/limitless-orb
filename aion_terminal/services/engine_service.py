from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import WebSocket

from aion_terminal.app.config import settings
from aion_terminal.services.alerts_service import check_alerts, dispatch_alerts
from aion_terminal.services.ingestion_service import ingest_symbol
from aion_terminal.storage import repositories
from aion_terminal.storage.db import bootstrap_schema, get_connection

logger = logging.getLogger(__name__)


def levels_without_curve(levels: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in levels.items() if k not in {"curve", "acceleration_zones"}}


def _get_conn():
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    return conn


def compute_or_load_levels(ticker: str, runtime_state, expiry_override: str | None = None) -> dict[str, Any]:
    if ticker in runtime_state.latest_levels and not expiry_override:
        return runtime_state.latest_levels[ticker]

    conn = _get_conn()
    try:
        levels = ingest_symbol(
            conn,
            symbol=ticker,
            token=settings.marketdata_token,
            api_url_template=settings.api_url_template,
            dte_max=settings.dte_max,
        )
        if expiry_override and expiry_override != "combined":
            rows = repositories.query_latest_chain(conn, ticker)
            filtered = [r for r in rows if r.get("expiry") == expiry_override]
            if filtered:
                from aion_terminal.features.dealer import compute_levels

                levels = compute_levels(filtered, levels.get("spot", 0.0), symbol=ticker)
                levels["curve"] = [
                    {"strike": p["strike"], "net_exposure": p["net_exposure"], "expiry": expiry_override}
                    for p in levels.get("curve", [])
                ]
        else:
            rows = repositories.query_latest_chain(conn, ticker)
            strike_expiry: dict[float, str] = {}
            for r in rows:
                strike, expiry = r.get("strike"), r.get("expiry")
                if strike and expiry:
                    if strike not in strike_expiry or expiry < strike_expiry[strike]:
                        strike_expiry[strike] = expiry
            levels["curve"] = [
                {
                    "strike": p["strike"],
                    "net_exposure": p["net_exposure"],
                    "expiry": strike_expiry.get(p["strike"], "combined"),
                }
                for p in levels.get("curve", [])
            ]

        runtime_state.latest_levels[ticker] = levels
        return levels
    finally:
        conn.close()


def query_expiries(symbol: str) -> list[str]:
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
                previous = repositories.query_previous_levels(conn, ticker)
                alerts = check_alerts(levels, previous)
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
