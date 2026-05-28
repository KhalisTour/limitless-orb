from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aion_terminal.app.config import settings
from aion_terminal.data_sources.marketdata import (
    MarketDataClient,
    _extract_quote_price,
    default_recent_window,
    discover_relevant_expiries,
    group_chain_by_expiry,
    normalize_chain_snapshots,
    normalize_underlying_bars,
)
from aion_terminal.services.snapshot_service import (
    build_levels_from_grouped_chain,
    build_levels_snapshot,
    ensure_levels_payload,
)
from aion_terminal.storage import repositories
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.utils.math_utils import as_float
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
    grouped_chain: dict[str, dict[str, Any]] = field(default_factory=dict)
    attempted_expiries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _open_conn():
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    return conn


def _client(token: str | None = None) -> MarketDataClient:
    return MarketDataClient(token=token or settings.marketdata_token)


def refresh_one_symbol_from_db(symbol: str, conn=None) -> RefreshResult:
    symbol = symbol.upper()
    local_conn = conn is None
    conn = conn or _open_conn()
    result = RefreshResult(symbol=symbol, ok=True)

    try:
        rows = repositories.query_latest_raw_chain_snapshot_rows(conn, symbol)
        result.grouped_chain = group_chain_by_expiry(rows)
        result.chain_rows_inserted = 0
        result.legacy_chain_inserted = 0

        spot = 0.0
        for row in rows:
            if row.underlying_price is not None:
                spot = float(row.underlying_price)
                break
        result.quote_price = spot or result.quote_price

        if result.grouped_chain:
            result.levels = build_levels_from_grouped_chain(result.grouped_chain, symbol=symbol, spot=spot)
        else:
            logger.warning("refresh_from_db no grouped chain for symbol=%s; falling back to legacy snapshot", symbol)
            result.levels = build_levels_snapshot(conn, symbol=symbol, spot=spot)
            result.levels = {
                **result.levels,
                "combined_levels": result.levels,
                "expiry_levels": {},
                "grouped_chain": {},
            }

        return result
    except Exception as exc:  # pragma: no cover
        logger.exception("refresh_from_db unexpected failure symbol=%s", symbol)
        return RefreshResult(symbol=symbol, ok=False, errors=[f"unexpected:{exc.__class__.__name__}"])
    finally:
        if local_conn:
            conn.close()


def refresh_one_symbol(symbol: str, conn=None, *, dte_max: int | None = None, skip_bars: bool = False, skip_chain: bool = False, no_option_history: bool = False) -> RefreshResult:
    symbol = symbol.upper()
    dte_max = dte_max if dte_max is not None else settings.dte_max
    local_conn = conn is None
    conn = conn or _open_conn()
    client = _client()

    result = RefreshResult(symbol=symbol, ok=True)
    now = utc_now_iso()

    try:
        if not skip_chain:
            quote = client.fetch_latest_quote(symbol)
            if quote.ok:
                result.quote_price = _extract_quote_price(quote.payload)
            elif quote.error == "unauthorized":
                result.errors.append("quote_unauthorized")
                logger.warning("ingestion quote unauthorized symbol=%s status=%s", symbol, quote.status_code)
            else:
                result.errors.append(f"quote_error:{quote.error}")
                logger.warning("ingestion quote failed symbol=%s error=%s", symbol, quote.error)

        if not skip_bars:
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

        expiry_candidates: list[Any] = []
        if not skip_chain:
            expiry_candidates, expiry_warning = discover_relevant_expiries(client, symbol)
            if expiry_warning:
                result.errors.append(expiry_warning)
                logger.info("ingestion expiry discovery symbol=%s warning=%s", symbol, expiry_warning)

        result.attempted_expiries = [e.expiry for e in expiry_candidates]
        logger.info(
            "ingestion expiry fetch symbol=%s eligible=%d selected=%s",
            symbol,
            len(expiry_candidates),
            result.attempted_expiries,
        )

        spot = result.quote_price
        research_rows = []
        legacy_rows: list[dict[str, Any]] = []

        if not skip_chain:
            for candidate in expiry_candidates:
                chain_fetch = client.fetch_options_chain(symbol, expiration=candidate.expiry)
                if not chain_fetch.ok:
                    if chain_fetch.error == "unauthorized":
                        result.errors.append(f"chain_unauthorized:{candidate.expiry}")
                        logger.warning("ingestion chain unauthorized symbol=%s expiry=%s dte=%s", symbol, candidate.expiry, candidate.dte)
                    else:
                        result.errors.append(f"chain_error:{candidate.expiry}:{chain_fetch.error}")
                        logger.warning(
                            "ingestion chain failed symbol=%s expiry=%s dte=%s error=%s",
                            symbol,
                            candidate.expiry,
                            candidate.dte,
                            chain_fetch.error,
                        )
                    continue

                rows, legacy, spot = normalize_chain_snapshots(
                    symbol=symbol,
                    payload=chain_fetch.payload if isinstance(chain_fetch.payload, dict) else {},
                    dte_max=dte_max,
                    fallback_spot=spot,
                )
                research_rows.extend(rows)
                legacy_rows.extend(legacy)

        result.grouped_chain = group_chain_by_expiry(research_rows)
        result.chain_rows_inserted = repositories.insert_chain_snapshots(conn, research_rows)
        result.legacy_chain_inserted = repositories.save_contracts(conn, legacy_rows)

        if result.grouped_chain:
            result.levels = build_levels_from_grouped_chain(result.grouped_chain, symbol=symbol, spot=spot or 0.0)
        elif legacy_rows:
            logger.warning("dealer compute using legacy compatibility rows for %s", symbol)
            result.levels = build_levels_snapshot(conn, symbol, spot or 0.0)
            result.levels = {
                **result.levels,
                "combined_levels": result.levels,
                "expiry_levels": {},
                "grouped_chain": {},
            }

        if result.levels:
            repositories.save_levels(conn, result.levels["combined_levels"], timestamp=now)

        if result.errors and not (result.chain_rows_inserted or result.bars_upserted):
            result.ok = False

        logger.info(
            "ingestion refresh symbol=%s ok=%s expiries=%s grouped_expiries=%s chain_rows=%s legacy_rows=%s bars=%s errors=%s",
            symbol,
            result.ok,
            len(result.attempted_expiries),
            list(result.grouped_chain.keys()),
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
    out: dict[str, RefreshResult] = {}
    conn = _open_conn()
    try:
        for idx, symbol in enumerate(symbols):
            try:
                out[symbol.upper()] = refresh_one_symbol(symbol, conn=conn)
            except Exception as exc:  # pragma: no cover
                logger.exception("ingestion refresh_universe hard failure symbol=%s", symbol)
                out[symbol.upper()] = RefreshResult(symbol=symbol.upper(), ok=False, errors=[f"hard_failure:{exc}"])
            if idx < len(symbols) - 1:
                time.sleep(2)
        return out
    finally:
        conn.close()


def backfill_underlying_bars(symbol: str, timeframe: str, start_date: str, end_date: str) -> RefreshResult:
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
    conn = _open_conn()
    client = _client()

    try:
        history = client.fetch_option_history(contract_symbol, start_date, end_date)
        if not history.ok:
            if history.status_code == 404 or history.error == "http_404":
                logger.info(
                    "option history not available (404) for contract=%s start=%s end=%s; treating as non-fatal",
                    contract_symbol,
                    start_date,
                    end_date,
                )
                return RefreshResult(symbol=contract_symbol, ok=True, option_history_rows=0)

            reason = "option_history_unauthorized" if history.error == "unauthorized" else f"option_history_error:{history.error}"
            logger.warning(
                "ingestion backfill option history failed contract=%s start=%s end=%s reason=%s",
                contract_symbol,
                start_date,
                end_date,
                reason,
            )
            return RefreshResult(symbol=contract_symbol, ok=False, errors=[reason])

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
    _ = api_url_template
    _token_backup = settings.marketdata_token
    try:
        if token:
            settings.marketdata_token = token
        result = refresh_one_symbol(symbol, conn=conn, dte_max=dte_max)
        if result.levels:
            return result.levels

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
            return ensure_levels_payload(dict(latest), conn=conn, symbol=symbol.upper(), spot=as_float(latest["spot"]))

        fallback = {"symbol": symbol.upper(), "spot": 0.0, "king_node": 0.0, "call_wall": None, "put_wall": None, "regime": "range"}
        return ensure_levels_payload(fallback, conn=conn, symbol=symbol.upper(), spot=0.0)
    finally:
        settings.marketdata_token = _token_backup
