from __future__ import annotations

from typing import Any

from aion_terminal.features.dealer import compute_levels
from aion_terminal.storage import repositories


def build_levels_snapshot(conn, symbol: str, spot: float) -> dict[str, Any]:
    """Build and return level snapshot from latest chain rows."""
    latest = repositories.query_latest_chain(conn, symbol)
    return compute_levels(latest, spot, symbol=symbol)
