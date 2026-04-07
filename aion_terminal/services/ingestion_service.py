from __future__ import annotations

import logging
from typing import Any

from aion_terminal.data_sources.marketdata import fetch_and_normalize
from aion_terminal.services.snapshot_service import build_levels_snapshot
from aion_terminal.storage import repositories
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)


def ingest_symbol(conn, *, symbol: str, token: str, api_url_template: str, dte_max: int) -> dict[str, Any]:
    """Fetch, persist, compute, and persist computed levels for a symbol."""
    contracts, spot = fetch_and_normalize(symbol, token, api_url_template, dte_max=dte_max)
    inserted = repositories.save_contracts(conn, contracts)
    levels = build_levels_snapshot(conn, symbol, spot)
    repositories.save_levels(conn, levels, timestamp=utc_now_iso())
    logger.info("Ingested symbol=%s contracts_inserted=%s", symbol, inserted)
    return levels
