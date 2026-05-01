from __future__ import annotations

import threading

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aion_terminal.api import (
    routes_agents,
    routes_backtests,
    routes_contracts,
    routes_dashboard,
    routes_rankings,
    routes_snapshot,
    routes_tags,
    routes_universe,
)
from aion_terminal.app.config import settings
from aion_terminal.app.dependencies import runtime_state
from aion_terminal.services.engine_service import pipeline_loop
from aion_terminal.utils.logging_utils import configure_logging

configure_logging()

app = FastAPI(title=settings.app_name)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(routes_agents.router)
app.include_router(routes_dashboard.router)
app.include_router(routes_snapshot.router)
app.include_router(routes_universe.router)
app.include_router(routes_rankings.router)
app.include_router(routes_contracts.router)
app.include_router(routes_backtests.router)
app.include_router(routes_tags.router)


@app.on_event("startup")
def startup_event() -> None:
    thread = threading.Thread(target=pipeline_loop, args=(runtime_state,), daemon=True)
    thread.start()


if __name__ == "__main__":
    uvicorn.run("aion_terminal.app.main:app", host="0.0.0.0", port=8000, reload=False)
