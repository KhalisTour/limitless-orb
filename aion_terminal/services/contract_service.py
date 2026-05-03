from __future__ import annotations

from dataclasses import asdict
import logging

from aion_terminal.app.config import settings
from aion_terminal.features.contracts import score_and_rank_contracts
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.storage.repositories import query_latest_chain
from aion_terminal.utils.math_utils import as_float

logger = logging.getLogger(__name__)

SCHEMA_PATH = "aion_terminal/storage/schema.sql"


def get_contract_recommendation(
    symbol: str,
    bias: str,
    dte_min: int = 0,
    dte_max: int = 21,
    budget: float | None = None,
) -> dict:
    symbol = symbol.upper()
    conn = None
    try:
        conn = get_connection(settings.db_path)
        bootstrap_schema(conn, SCHEMA_PATH)

        chain = query_latest_chain(conn, symbol)
        if not chain:
            return {
                "symbol": symbol,
                "bias": bias,
                "spot": 0.0,
                "best": None,
                "safer": None,
                "convex": None,
                "all_scored": [],
                "warnings": ["no_chain_data"],
                "error": f"No chain data for {symbol}",
            }

        spot = as_float(chain[0].get("underlying_price"))
        rec = score_and_rank_contracts(
            conn,
            symbol=symbol,
            bias=bias,
            spot=spot,
            dte_min=dte_min,
            dte_max=dte_max,
            budget=budget,
        )

        return {
            "symbol": rec.symbol,
            "bias": rec.bias,
            "spot": rec.spot,
            "best": asdict(rec.best) if rec.best else None,
            "safer": asdict(rec.safer) if rec.safer else None,
            "convex": asdict(rec.convex) if rec.convex else None,
            "all_scored": [asdict(c) for c in rec.all_scored],
            "warnings": rec.warnings,
            "error": None,
        }
    except Exception as exc:  # pragma: no cover
        logger.exception("get_contract_recommendation failed symbol=%s", symbol)
        return {
            "symbol": symbol,
            "bias": bias,
            "spot": 0.0,
            "best": None,
            "safer": None,
            "convex": None,
            "all_scored": [],
            "warnings": [],
            "error": str(exc),
        }
    finally:
        if conn is not None:
            conn.close()
