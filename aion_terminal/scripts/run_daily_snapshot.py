from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv

from aion_terminal.app.config import settings
from aion_terminal.features.contracts import score_and_rank_contracts
from aion_terminal.features.technical import build_technical_features
from aion_terminal.services.ingestion_service import refresh_one_symbol, refresh_one_symbol_from_db
from aion_terminal.services.ranking_service import rank_universe
from aion_terminal.signals.setups import evaluate_symbol_snapshot
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.storage.repositories import (
    insert_feature_snapshots,
    insert_setup_candidates,
    query_latest_raw_chain_snapshot_ts,
    query_underlying_bars_count,
)
from aion_terminal.models.dto import FeatureSnapshotRecord, SetupCandidateRecord
from aion_terminal.utils.time_utils import utc_now_iso
from aion_terminal.utils.math_utils import as_float

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily snapshot orchestration for watchlist symbols.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols override watchlist")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-symbols", type=int, default=3)
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    parser.add_argument("--skip-refresh-if-recent-minutes", type=int, default=30)
    parser.add_argument("--chain-only", action="store_true")
    parser.add_argument("--bars-only", action="store_true")
    parser.add_argument("--no-option-history", action="store_true")
    parser.add_argument("--no-contracts", action="store_true")
    parser.add_argument("--no-setups", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def _parse_csv(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _prioritize_symbols(symbols: list[str], max_symbols: int) -> list[str]:
    ranked = rank_universe(symbols=symbols)
    ordered = [r.symbol for r in ranked if r.symbol in symbols]
    leftovers = [s for s in symbols if s not in ordered]
    out = (ordered + leftovers)[:max_symbols]
    return out


def _symbol_recently_refreshed(conn, symbol: str, minutes: int) -> bool:
    latest_ts = query_latest_raw_chain_snapshot_ts(conn, symbol)
    if not latest_ts:
        return False
    try:
        last_refresh = datetime.fromisoformat(latest_ts)
    except ValueError:
        return False
    return datetime.now(last_refresh.tzinfo or None) - last_refresh < timedelta(minutes=minutes)


def _build_feature_snapshots_for_refresh(symbol: str, refresh) -> list[FeatureSnapshotRecord]:
    if not refresh.grouped_chain or not refresh.levels:
        return []

    expiry_levels = refresh.levels.get("expiry_levels") or {}
    if not expiry_levels:
        return []

    snapshots: list[FeatureSnapshotRecord] = []
    for expiry in sorted(expiry_levels.keys()):
        if not expiry:
            continue

        level = expiry_levels[expiry]
        bucket = refresh.grouped_chain.get(expiry, {})
        dte = bucket.get("dte_min") if bucket.get("dte_min") is not None else bucket.get("dte_max")

        snapshots.append(
            FeatureSnapshotRecord(
                snapshot_ts=utc_now_iso(),
                symbol=symbol,
                expiry=expiry,
                dte=dte,
                spot=float(level.get("spot") or 0.0),
                regime=str(level.get("regime") or "neutral"),
                king_node=float(level.get("king_node") or 0.0),
                call_wall=level.get("call_wall"),
                put_wall=level.get("put_wall"),
                flip_zone=level.get("flip_zone"),
                features_json=json.dumps({"distances": level.get("distances", {})}),
            )
        )

    return snapshots


def _open_conn():
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    return conn


def _load_daily_bars(conn, symbol: str):
    rows = conn.execute(
        "SELECT symbol, timeframe, bar_ts, open, high, low, close, volume, vwap FROM underlying_bars WHERE symbol = ? ORDER BY bar_ts DESC LIMIT 60",
        (symbol,),
    ).fetchall()
    from aion_terminal.models.dto import UnderlyingBarRecord

    return [UnderlyingBarRecord(symbol=r["symbol"], timeframe=r["timeframe"], bar_ts=r["bar_ts"], open=as_float(r["open"]), high=as_float(r["high"]), low=as_float(r["low"]), close=as_float(r["close"]), volume=r["volume"], vwap=as_float(r["vwap"])) for r in reversed(rows)]


def main() -> int:
    args = parse_args()
    load_dotenv()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.chain_only and args.bars_only:
        logger.warning("both --chain-only and --bars-only set; using full refresh semantics")

    symbols = _parse_csv(args.symbols) if args.symbols else [s.upper() for s in settings.watchlist]
    if args.limit is not None:
        symbols = symbols[: max(0, args.limit)]

    symbols = _prioritize_symbols(symbols, max_symbols=max(1, args.max_symbols)) if symbols else []

    conn = _open_conn()
    try:
        for index, symbol in enumerate(symbols):
            contracts_inserted = 0
            expiries_found = 0
            setup_count = 0
            top_ranking = None
            success = True
            skipped_recent = False
            refreshed = False
            bars_used = False
            errors: list[str] = []
            try:
                if _symbol_recently_refreshed(conn, symbol, args.skip_refresh_if_recent_minutes):
                    refresh = refresh_one_symbol_from_db(symbol, conn=conn)
                    skipped_recent = True
                else:
                    refresh = refresh_one_symbol(
                        symbol,
                        conn=conn,
                        skip_bars=args.chain_only,
                        skip_chain=args.bars_only,
                        no_option_history=args.no_option_history,
                    )
                    refreshed = True

                contracts_inserted = refresh.chain_rows_inserted
                expiries_found = len(refresh.grouped_chain)
                bars_used = refresh.bars_upserted > 0 or query_underlying_bars_count(conn, symbol) > 0
                errors = list(refresh.errors)

                feature_snapshots = _build_feature_snapshots_for_refresh(symbol, refresh)
                if feature_snapshots:
                    try:
                        insert_feature_snapshots(conn, feature_snapshots)
                    except Exception:
                        logger.exception("feature snapshot insertion failed symbol=%s", symbol)
                else:
                    logger.info("no expiries available; skipping feature snapshot")

                if not args.no_setups and refresh.levels:
                    technical_features, technical_state = build_technical_features(_load_daily_bars(conn, symbol), "D")
                    setups = evaluate_symbol_snapshot(
                        symbol=symbol,
                        dealer_features=refresh.levels.get("combined_levels") or refresh.levels,
                        technical_features=technical_features,
                        technical_state=technical_state,
                        narrative_tags=[],
                    )
                    setup_records = []
                    for idx, sig in enumerate(setups, start=1):
                        setup_records.append(
                            SetupCandidateRecord(
                                candidate_id=f"{symbol}-{idx}-{utc_now_iso()}",
                                as_of_ts=utc_now_iso(),
                                symbol=symbol,
                                setup_class=sig.setup_class,
                                direction=sig.bias,
                                timeframe="D",
                                expiry=None,
                                option_type="call" if sig.bias == "bullish" else "put",
                                strike=None,
                                score=sig.confidence_raw,
                                rank=idx,
                                confidence=sig.confidence_raw,
                                rationale_json=sig.reason_json,
                                status="new",
                            )
                        )
                    try:
                        setup_count = insert_setup_candidates(conn, setup_records)
                        logger.info("setup candidates inserted symbol=%s count=%s", symbol, setup_count)
                    except Exception as exc:
                        logger.exception("setup candidate insertion failed symbol=%s err=%s", symbol, exc)

                if not args.no_contracts and refresh.quote_price is not None:
                    rec = score_and_rank_contracts(conn, symbol=symbol, bias="bullish", spot=refresh.quote_price)
                    top_ranking = rec.best.contract_symbol if rec.best else None

                success = refresh.ok
            except Exception as exc:
                success = False
                errors.append(str(exc))
                logger.exception("daily snapshot failure symbol=%s", symbol)

            summary_errors = ";".join(errors) if errors else ""
            print(
                f"{symbol} | skipped_recent={skipped_recent} | refreshed={refreshed} | "
                f"chain_rows={contracts_inserted} | bars_used={bars_used} | setups={setup_count} | "
                f"contracts={1 if top_ranking else 0} | errors={summary_errors}"
            )

            if index < len(symbols) - 1 and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
