from __future__ import annotations

import logging
from typing import Any

from aion_terminal.features.dealer import compute_combined_and_expiry_levels, compute_levels
from aion_terminal.storage import repositories

logger = logging.getLogger(__name__)


def build_levels_snapshot(conn, symbol: str, spot: float) -> dict[str, Any]:
    """Legacy fallback snapshot built from compatibility rows."""
    latest = repositories.query_latest_chain(conn, symbol)
    return compute_levels(latest, spot, symbol=symbol)


def build_levels_from_grouped_chain(
    grouped_chain: dict[str, dict[str, Any]],
    symbol: str,
    spot: float,
) -> dict[str, Any]:
    """Primary dealer snapshot preserving combined and expiry-aware structures."""
    if not grouped_chain:
        logger.warning("dealer grouped-chain empty for symbol=%s; falling back to legacy flat compute", symbol)
        combined = compute_levels([], spot, symbol=symbol)
        return {
            **combined,
            "combined_levels": combined,
            "expiry_levels": {},
            "grouped_chain": {},
        }

    combined_levels, expiry_levels = compute_combined_and_expiry_levels(grouped_chain, spot=spot, symbol=symbol)
    return {
        **combined_levels,
        "combined_levels": combined_levels,
        "expiry_levels": expiry_levels,
        "grouped_chain": grouped_chain,
    }


def ensure_levels_payload(
    levels: dict[str, Any],
    conn,
    symbol: str,
    spot: float,
) -> dict[str, Any]:
    """Ensure payload has combined_levels/expiry_levels/grouped_chain, with clear fallback logging."""
    if levels.get("combined_levels") is not None and levels.get("expiry_levels") is not None:
        return levels

    logger.warning("dealer payload missing grouped structures for %s; using legacy fallback rows", symbol)
    legacy = build_levels_snapshot(conn, symbol=symbol, spot=spot)
    return {
        **legacy,
        "combined_levels": legacy,
        "expiry_levels": {},
        "grouped_chain": {},
    }
