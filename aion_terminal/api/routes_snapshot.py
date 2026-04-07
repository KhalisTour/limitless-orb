from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from aion_terminal.app.dependencies import runtime_state
from aion_terminal.services.engine_service import (
    compute_or_load_levels,
    levels_without_curve,
    query_expiries,
    websocket_register,
)

router = APIRouter(tags=["snapshot"])


@router.get("/levels/{ticker}")
def get_levels(ticker: str):
    try:
        levels = compute_or_load_levels(ticker.upper(), runtime_state)
        return levels_without_curve(levels)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/curve/{ticker}")
def get_curve(ticker: str, expiry: str | None = None):
    try:
        levels = compute_or_load_levels(ticker.upper(), runtime_state)
        curve_levels = compute_or_load_levels(ticker.upper(), runtime_state, expiry_override=expiry)
        return {"symbol": levels.get("symbol"), "spot": levels.get("spot"), "curve": curve_levels.get("curve", [])}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/expiries/{ticker}")
def get_expiries(ticker: str):
    try:
        return {"symbol": ticker.upper(), "expiries": query_expiries(ticker.upper())}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.websocket("/ws/{ticker}")
async def websocket_levels(websocket: WebSocket, ticker: str):
    await websocket_register(websocket, ticker.upper(), runtime_state)
    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in runtime_state.active_connections:
            runtime_state.active_connections.remove(websocket)
