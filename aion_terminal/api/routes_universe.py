from __future__ import annotations

from fastapi import APIRouter

from aion_terminal.app.config import settings

router = APIRouter(tags=["universe"])


@router.get("/universe")
def get_universe():
    return {"watchlist": settings.watchlist}
