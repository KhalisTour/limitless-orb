from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict

from dotenv import load_dotenv

from aion_terminal.app.config import settings
from aion_terminal.features.contracts import score_and_rank_contracts
from aion_terminal.features.technical import build_technical_features
from aion_terminal.services.ingestion_service import refresh_one_symbol
from aion_terminal.services.ranking_service import rank_universe
from aion_terminal.signals.setups import evaluate_symbol_snapshot
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.storage.repositories import (
    insert_feature_snapshots,
    insert_setup_candidates,
)
from aion_terminal.models.dto import FeatureSnapshotRecord, SetupCandidateRecord
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily snapshot orchestration for watchlist symbols.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols override watchlist")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-symbols", type=int, default=5)
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


def _open_conn():
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    return conn


def main() -> int:
    args = parse_args()
    load_dotenv()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    symbols = _parse_csv(args.symbols) if args.symbols else [s.upper() for s in settings.watchlist]
    if args.limit is not None:
        symbols = symbols[: max(0, args.limit)]

    symbols = _prioritize_symbols(symbols, max_symbols=max(1, args.max_symbols)) if symbols else []

    conn = _open_conn()
    try:
        for symbol in symbols:
            contracts_inserted = 0
            expiries_found = 0
            setup_count = 0
            top_ranking = None
            success = True
            try:
                refresh = refresh_one_symbol(symbol, conn=conn)
                contracts_inserted = refresh.chain_rows_inserted
                expiries_found = len(refresh.grouped_chain)

                if refresh.levels:
                    combined = refresh.levels.get("combined_levels") or refresh.levels
                    feature_payload = FeatureSnapshotRecord(
                        snapshot_ts=utc_now_iso(),
                        symbol=symbol,
                        expiry=None,
                        dte=None,
                        spot=float(combined.get("spot") or 0.0),
                        regime=str(combined.get("regime") or "neutral"),
                        king_node=float(combined.get("king_node") or 0.0),
                        call_wall=combined.get("call_wall"),
                        put_wall=combined.get("put_wall"),
                        flip_zone=None,
                        features_json=json.dumps({"distances": combined.get("distances", {})}),
                    )
                    insert_feature_snapshots(conn, [feature_payload])

                if not args.no_setups and refresh.levels:
                    technical_features, technical_state = build_technical_features([], "D")
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
                    setup_count = insert_setup_candidates(conn, setup_records)

                if not args.no_contracts and refresh.quote_price:
                    rec = score_and_rank_contracts(conn, symbol=symbol, bias="bullish", spot=refresh.quote_price)
                    top_ranking = rec.best.contract_symbol if rec.best else None

                success = refresh.ok
            except Exception as exc:
                success = False
                logger.exception("daily snapshot failure symbol=%s", symbol)
                print(f"{symbol} | failure | contracts=0 expiries=0 setups=0 top=None error={exc}")

            if success:
                print(
                    f"{symbol} | success | contracts={contracts_inserted} expiries={expiries_found} "
                    f"setups={setup_count} top={top_ranking}"
                )
            else:
                print(
                    f"{symbol} | failure | contracts={contracts_inserted} expiries={expiries_found} "
                    f"setups={setup_count} top={top_ranking}"
                )
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
