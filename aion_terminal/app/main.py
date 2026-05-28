from __future__ import annotations

import logging
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from aion_terminal.api import (
    routes_agents,
    routes_arbitration,
    routes_backtests,
    routes_cone,
    routes_contracts,
    routes_dashboard,
    routes_health,
    routes_nitter,
    routes_rankings,
    routes_rs,
    routes_snapshot,
    routes_tags,
    routes_trades,
    routes_universe,
)
from aion_terminal.app.config import settings
from aion_terminal.app.dependencies import runtime_state
from aion_terminal.services.engine_service import pipeline_loop
from aion_terminal.utils.logging_utils import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(routes_agents.router)
app.include_router(routes_arbitration.router)
app.include_router(routes_dashboard.router)
app.include_router(routes_snapshot.router)
app.include_router(routes_universe.router)
app.include_router(routes_rankings.router)
app.include_router(routes_contracts.router)
app.include_router(routes_backtests.router)
app.include_router(routes_tags.router)
app.include_router(routes_rs.router)
app.include_router(routes_cone.router)
app.include_router(routes_health.router)
app.include_router(routes_trades.router)
app.include_router(routes_nitter.router)

app.mount(
    "/static",
    StaticFiles(directory="aion_terminal/static", html=True),
    name="static",
)


@app.on_event("startup")
def startup_event() -> None:
    if settings.cache_only or not settings.marketdata_enabled:
        logger.info("cache-only mode enabled; background ingestion pipeline disabled")
        return

    thread = threading.Thread(target=pipeline_loop, args=(runtime_state,), daemon=True)
    thread.start()
    logger.info("background ingestion pipeline started")


if __name__ == "__main__":
    uvicorn.run("aion_terminal.app.main:app", host="0.0.0.0", port=8000, reload=False)
