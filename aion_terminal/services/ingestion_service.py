from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aion_terminal.app.config import settings
from aion_terminal.data_sources.marketdata import (
    MarketDataClient,
    default_recent_window,
    next_monthly_expiries,
    normalize_chain_snapshots,
    normalize_underlying_bars,
    _extract_quote_price,
)
from aion_terminal.services.snapshot_service import build_levels_snapshot
from aion_terminal.storage import repositories
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RefreshResult:
    symbol: str
    ok: bool
    quote_price: float | None = None
    chain_rows_inserted: int = 0
    legacy_chain_inserted: int = 0
    bars_upserted: int = 0
    option_history_rows: int = 0
    levels: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


def _open_conn():
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    return conn


def _client(token: str | None = None) -> MarketDataClient:
    return MarketDataClient(token=token or settings.marketdata_token)


def refresh_one_symbol(symbol: str, conn=None, *, dte_max: int | None = None) -> RefreshResult:
    """Refresh quote, bars, and option chain for one symbol with partial-success resilience."""
    symbol = symbol.upper()
    dte_max = dte_max if dte_max is not None else settings.dte_max
    local_conn = conn is None
    conn = conn or _open_conn()
    client = _client()

    result = RefreshResult(symbol=symbol, ok=True)
    now = utc_now_iso()

    try:
        quote = client.fetch_latest_quote(symbol)
        if quote.ok:
            result.quote_price = _extract_quote_price(quote.payload)
        elif quote.error == "unauthorized":
            result.errors.append("quote_unauthorized")
            logger.warning("ingestion quote unauthorized symbol=%s status=%s", symbol, quote.status_code)
        else:
            result.errors.append(f"quote_error:{quote.error}")
            logger.warning("ingestion quote failed symbol=%s error=%s", symbol, quote.error)

        start_date, end_date = default_recent_window(days=10)
        bars_fetch = client.fetch_underlying_bars(symbol, timeframe="D", start_date=start_date, end_date=end_date)
        if bars_fetch.ok:
            bars = normalize_underlying_bars(symbol, "D", bars_fetch.payload)
            result.bars_upserted = repositories.upsert_underlying_bars(conn, bars)
        elif bars_fetch.error == "unauthorized":
            result.errors.append("bars_unauthorized")
            logger.warning("ingestion bars unauthorized symbol=%s status=%s", symbol, bars_fetch.status_code)
        else:
            result.errors.append(f"bars_error:{bars_fetch.error}")
            logger.warning("ingestion bars failed symbol=%s error=%s", symbol, bars_fetch.error)

        spot = result.quote_price
        research_rows = []
        legacy_rows: list[dict[str, Any]] = []

        for expiry in next_monthly_expiries(3):
            chain_fetch = client.fetch_options_chain(symbol, expiration=expiry)
            if not chain_fetch.ok:
                if chain_fetch.error == "unauthorized":
                    result.errors.append(f"chain_unauthorized:{expiry}")
                    logger.warning("ingestion chain unauthorized symbol=%s expiry=%s", symbol, expiry)
                else:
                    result.errors.append(f"chain_error:{expiry}:{chain_fetch.error}")
                    logger.warning("ingestion chain failed symbol=%s expiry=%s error=%s", symbol, expiry, chain_fetch.error)
                continue

            rows, legacy, spot = normalize_chain_snapshots(
                symbol=symbol,
                payload=chain_fetch.payload if isinstance(chain_fetch.payload, dict) else {},
                dte_max=dte_max,
                fallback_spot=spot,
            )
            research_rows.extend(rows)
            legacy_rows.extend(legacy)

        result.chain_rows_inserted = repositories.insert_chain_snapshots(conn, research_rows)
        result.legacy_chain_inserted = repositories.save_contracts(conn, legacy_rows)

        if legacy_rows:
            levels = build_levels_snapshot(conn, symbol, spot or 0.0)
            repositories.save_levels(conn, levels, timestamp=now)
            result.levels = levels

        if result.errors and not (result.chain_rows_inserted or result.bars_upserted):
            result.ok = False

        logger.info(
            "ingestion refresh symbol=%s ok=%s chain_rows=%s legacy_rows=%s bars=%s errors=%s",
            symbol,
            result.ok,
            result.chain_rows_inserted,
            result.legacy_chain_inserted,
            result.bars_upserted,
            result.errors,
        )
        return result
    except Exception as exc:  # pragma: no cover
        logger.exception("ingestion unexpected failure symbol=%s", symbol)
        return RefreshResult(symbol=symbol, ok=False, errors=[f"unexpected:{exc.__class__.__name__}"])
    finally:
        if local_conn:
            conn.close()


def refresh_universe(symbols: list[str]) -> dict[str, RefreshResult]:
    """Refresh a symbol universe. One failure never interrupts other symbols."""
    out: dict[str, RefreshResult] = {}
    conn = _open_conn()
    try:
        for symbol in symbols:
            try:
                out[symbol.upper()] = refresh_one_symbol(symbol, conn=conn)
            except Exception as exc:  # pragma: no cover
                logger.exception("ingestion refresh_universe hard failure symbol=%s", symbol)
                out[symbol.upper()] = RefreshResult(symbol=symbol.upper(), ok=False, errors=[f"hard_failure:{exc}"])
        return out
    finally:
        conn.close()


def backfill_underlying_bars(symbol: str, timeframe: str, start_date: str, end_date: str) -> RefreshResult:
    """Backfill historical underlying bars and persist into underlying_bars."""
    symbol = symbol.upper()
    conn = _open_conn()
    client = _client()

    try:
        bars_fetch = client.fetch_underlying_bars(symbol, timeframe, start_date, end_date)
        if not bars_fetch.ok:
            reason = "bars_unauthorized" if bars_fetch.error == "unauthorized" else f"bars_error:{bars_fetch.error}"
            logger.warning(
                "ingestion backfill bars failed symbol=%s timeframe=%s start=%s end=%s reason=%s",
                symbol,
                timeframe,
                start_date,
                end_date,
                reason,
            )
            return RefreshResult(symbol=symbol, ok=False, errors=[reason])

        bars = normalize_underlying_bars(symbol, timeframe, bars_fetch.payload)
        upserted = repositories.upsert_underlying_bars(conn, bars)
        logger.info(
            "ingestion backfill bars symbol=%s timeframe=%s start=%s end=%s upserted=%s",
            symbol,
            timeframe,
            start_date,
            end_date,
            upserted,
        )
        return RefreshResult(symbol=symbol, ok=True, bars_upserted=upserted)
    finally:
        conn.close()


def backfill_option_history(contract_symbol: str, start_date: str, end_date: str) -> RefreshResult:
    """Backfill option contract candle history if endpoint entitlement is available."""
    conn = _open_conn()
    client = _client()

    try:
        history = client.fetch_option_history(contract_symbol, start_date, end_date)
        if not history.ok:
            reason = "option_history_unauthorized" if history.error == "unauthorized" else f"option_history_error:{history.error}"
            logger.warning(
                "ingestion backfill option history failed contract=%s start=%s end=%s reason=%s",
                contract_symbol,
                start_date,
                end_date,
                reason,
            )
            return RefreshResult(symbol=contract_symbol, ok=False, errors=[reason])

        # Option history endpoint support varies by entitlement. Persist only when payload resembles bars.
        bars = normalize_underlying_bars(contract_symbol, "option", history.payload, source="marketdata_option")
        upserted = repositories.upsert_underlying_bars(conn, bars)
        logger.info(
            "ingestion backfill option history contract=%s start=%s end=%s rows=%s",
            contract_symbol,
            start_date,
            end_date,
            upserted,
        )
        return RefreshResult(symbol=contract_symbol, ok=True, option_history_rows=upserted)
    finally:
        conn.close()


def ingest_symbol(conn, *, symbol: str, token: str, api_url_template: str, dte_max: int) -> dict[str, Any]:
    """Legacy-compatible wrapper used by existing pipeline loop and snapshot endpoints."""
    _ = api_url_template  # maintained for signature compatibility
    _token_backup = settings.marketdata_token
    try:
        if token:
            settings.marketdata_token = token
        result = refresh_one_symbol(symbol, conn=conn, dte_max=dte_max)
        if result.levels:
            return result.levels

        # Fallback: try latest computed levels from db if ingestion had partial failures.
        latest = conn.execute(
            """
            SELECT symbol, spot, king_node, call_wall, put_wall, regime
            FROM computed_levels
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (symbol.upper(),),
        ).fetchone()
        if latest:
            return dict(latest)

        return {"symbol": symbol.upper(), "spot": 0.0, "king_node": 0.0, "call_wall": None, "put_wall": None, "regime": "range"}
    finally:
        settings.marketdata_token = _token_backup
