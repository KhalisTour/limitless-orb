"""Dashboard API routes."""

from __future__ import annotations

from fastapi import APIRouter

from aion_terminal.services.dashboard_service import get_dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def get_dashboard_endpoint():
    """Get unified dashboard.
    
    Returns all key data for frontend initialization:
    - watchlist
    - top ranked candidates (with degradation if needed)
    - latest macro brief summary
    - system status (counts, last update)
    - RS candidates (if available)
    
    No external API calls; reads from DB and saved files only.
    """
    return get_dashboard()
