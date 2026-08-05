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
from aion_terminal.services.ranking_service import infer_bias, rank_universe
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


def _technical_payload(features, state) -> dict:
    """Shape technical features into the keys the arbiter's scorer reads.

    ``arbitration.scoring.score_technical_agreement`` looks up ``ema_stack``,
    ``trend``, ``rvol``, ``compressed`` and ``pullback_depth``. Persisting the
    raw dataclass field names instead would leave every lookup returning None,
    which is what pinned the technical channel at a constant 0.5.
    """
    # An unavailable technical read must not stop the dealer snapshot being
    # persisted — the two are independent. Absent technicals simply leave the
    # channel at its neutral default rather than failing the whole symbol.
    if features is None or not hasattr(features, "ema_stack_state"):
        return {}
    return {
        "ema_stack": features.ema_stack_state,
        "trend": features.trend_state,
        "rvol": features.rvol,
        "compressed": bool(features.compressed),
        # The scorer tests `0 < pullback_depth < 0.05`, i.e. a fraction, while
        # the feature is carried as a percentage. Convert rather than emit a
        # value that is 100x out of range and can never match.
        "pullback_depth": (features.pullback_pct / 100.0) if features.pullback_pct else 0.0,
        "above_vwap": bool(getattr(state, "above_vwap", False)),
        "vwap_distance_pct": features.vwap_distance_pct,
        "atr": features.atr,
    }


def _build_feature_snapshots_for_refresh(symbol: str, refresh, technical: dict | None = None) -> list[FeatureSnapshotRecord]:
    if not refresh.grouped_chain or not refresh.levels:
        return []

    expiry_levels = refresh.levels.get("expiry_levels") or {}
    combined_levels = refresh.levels.get("combined_levels")
    if not expiry_levels and not combined_levels:
        return []

    # One timestamp for the whole batch so the combined row and the per-expiry
    # rows share a single (symbol, snapshot_ts) — this is the key the arbiter and
    # any consistency check join on.
    snapshot_ts = utc_now_iso()
    snapshots: list[FeatureSnapshotRecord] = []

    def _record(expiry: str, level: dict, dte) -> FeatureSnapshotRecord:
        return FeatureSnapshotRecord(
            snapshot_ts=snapshot_ts,
            symbol=symbol,
            expiry=expiry,
            dte=dte,
            spot=float(level.get("spot") or 0.0),
            regime=str(level.get("regime") or "neutral"),
            king_node=float(level.get("king_node") or 0.0),
            call_wall=level.get("call_wall"),
            put_wall=level.get("put_wall"),
            flip_zone=level.get("flip_zone"),
            features_json=json.dumps(
                {
                    "distances": level.get("distances", {}),
                    # The signed structure read (P0-4). Without these the arbiter
                    # only sees the collapsed `regime` string and has to
                    # re-derive direction from raw levels.
                    "gamma_state": level.get("gamma_state"),
                    "structural_bias": level.get("structural_bias"),
                    "king_proximity_pct": level.get("king_proximity_pct"),
                    "local_gamma_ratio": level.get("local_gamma_ratio"),
                    "flip_zone_status": level.get("flip_zone_status"),
                    **(technical or {}),
                }
            ),
        )

    # Combined-across-expiries map: the single source of truth the arbiter reads.
    if combined_levels:
        snapshots.append(_record("combined", combined_levels, None))

    for expiry in sorted(expiry_levels.keys()):
        if not expiry:
            continue
        level = expiry_levels[expiry]
        bucket = refresh.grouped_chain.get(expiry, {})
        dte = bucket.get("dte_min") if bucket.get("dte_min") is not None else bucket.get("dte_max")
        snapshots.append(_record(expiry, level, dte))

    return snapshots


def _open_conn():
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    return conn


def _load_daily_bars(conn, symbol: str, limit: int = 60):
    """Load a clean daily bar series for technical computation.

    Two corruptions have to be filtered out before the series is usable:

    * The table mixes timeframes (``1H``, ``4H``, ``15M`` alongside daily), and
      an unfiltered read would compute "daily" EMAs across intraday bars.
    * The same trading day is stored under more than one daily label (``D`` and
      ``1D``) from separate fetches, with slightly different closes and volumes.
      Unfiltered, a 60-row read returned as few as 35 distinct days, so ~40% of
      the series was repeats — smoothing the EMAs, deflating ATR through
      zero-range duplicate days, double-counting VWAP volume, and depressing
      RVOL against a corrupted average.

    Deduplicated newest-first by calendar day so the most recent write for a
    day wins, then returned oldest-first for the indicator functions.
    """
    from aion_terminal.models.dto import UnderlyingBarRecord

    rows = conn.execute(
        "SELECT symbol, timeframe, bar_ts, open, high, low, close, volume, vwap "
        "FROM underlying_bars WHERE symbol = ? AND UPPER(timeframe) IN ('D', '1D', 'DAILY') "
        "ORDER BY bar_ts DESC LIMIT ?",
        (symbol, limit * 4),
    ).fetchall()

    seen: set[str] = set()
    kept = []
    for r in rows:
        day = str(r["bar_ts"])[:10]
        if day in seen:
            continue
        seen.add(day)
        kept.append(r)
        if len(kept) >= limit:
            break

    return [
        UnderlyingBarRecord(
            symbol=r["symbol"],
            timeframe=r["timeframe"],
            bar_ts=r["bar_ts"],
            open=as_float(r["open"]),
            high=as_float(r["high"]),
            low=as_float(r["low"]),
            close=as_float(r["close"]),
            volume=r["volume"],
            vwap=as_float(r["vwap"]),
        )
        for r in reversed(kept)
    ]


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

                # Computed before the snapshot is built so the technical read can
                # be persisted alongside the dealer map — the arbiter scores what
                # is written here, not what is recomputed later for setups.
                technical_features, technical_state = build_technical_features(_load_daily_bars(conn, symbol), "D")

                feature_snapshots = _build_feature_snapshots_for_refresh(
                    symbol, refresh, _technical_payload(technical_features, technical_state)
                )
                if feature_snapshots:
                    try:
                        insert_feature_snapshots(conn, feature_snapshots)
                    except Exception:
                        logger.exception("feature snapshot insertion failed symbol=%s", symbol)
                else:
                    logger.info("no expiries available; skipping feature snapshot")

                inferred_bias: str | None = None
                if not args.no_setups and refresh.levels:
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

                    inferred_bias = infer_bias(
                        setup_bias=setups[0].bias if setups else None,
                        technical_state=technical_state,
                        dealer_features=refresh.levels.get("combined_levels") or refresh.levels,
                    )[0]

                if not args.no_contracts and refresh.quote_price is not None:
                    bias_for_contracts = inferred_bias
                    if bias_for_contracts is None and refresh.levels:
                        bias_for_contracts = infer_bias(
                            dealer_features=refresh.levels.get("combined_levels") or refresh.levels,
                        )[0]
                    if bias_for_contracts in ("bullish", "bearish"):
                        rec = score_and_rank_contracts(
                            conn, symbol=symbol, bias=bias_for_contracts, spot=refresh.quote_price
                        )
                        top_ranking = rec.best.contract_symbol if rec.best else None
                    else:
                        logger.info("neutral bias for symbol=%s; skipping contract scoring", symbol)

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
