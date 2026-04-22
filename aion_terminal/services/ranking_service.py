from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import sqlite3
from typing import Any

from aion_terminal.app.config import settings
from aion_terminal.features.contracts import ContractScore, score_and_rank_contracts
from aion_terminal.features.dealer import compute_levels
from aion_terminal.features.technical import TechnicalFeatures, TechnicalState, build_technical_features
from aion_terminal.models.dto import UnderlyingBarRecord
from aion_terminal.signals.setups import SetupSignal, evaluate_symbol_snapshot
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.storage.repositories import query_latest_chain
from aion_terminal.utils.math_utils import as_float
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)

SCHEMA_PATH = "aion_terminal/storage/schema.sql"
DEFAULT_DTE_MIN = 9
DEFAULT_DTE_MAX = 14
DEFAULT_MIN_CONFIDENCE = 0.30
BARS_LOOKBACK = 60


@dataclass(slots=True)
class SymbolRanking:
    symbol: str
    spot: float
    regime: str
    king_node: float
    call_wall: float | None
    put_wall: float | None
    signals: list[dict[str, Any]]
    best_contract: dict[str, Any] | None
    safer_contract: dict[str, Any] | None
    convex_contract: dict[str, Any] | None
    contract_warnings: list[str]
    dealer_distances: dict[str, Any]
    bar_count: int
    ema_stack: str
    trend: str
    rvol: float
    compressed: bool
    ranked_at: str
    errors: list[str] = field(default_factory=list)


def _open_connection() -> sqlite3.Connection:
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    return conn


def serialize_signal(signal: SetupSignal) -> dict[str, Any]:
    payload = asdict(signal)
    reason = payload.get("reason_json")
    if isinstance(reason, str):
        try:
            payload["reason_json"] = json.loads(reason)
        except json.JSONDecodeError:
            payload["reason_json"] = [reason]
    return payload


def serialize_contract(contract: ContractScore | None) -> dict[str, Any] | None:
    if contract is None:
        return None
    return asdict(contract)


def _load_underlying_bars(conn: sqlite3.Connection, symbol: str) -> list[UnderlyingBarRecord]:
    rows = conn.execute(
        """
        SELECT symbol, timeframe, bar_ts, open, high, low, close, volume, vwap
        FROM underlying_bars
        WHERE symbol = ?
        ORDER BY bar_ts DESC
        LIMIT ?
        """,
        (symbol, BARS_LOOKBACK),
    ).fetchall()

    reversed_rows = list(reversed(rows))
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
        for r in reversed_rows
    ]


def _default_ranking(symbol: str) -> SymbolRanking:
    return SymbolRanking(
        symbol=symbol,
        spot=0.0,
        regime="neutral",
        king_node=0.0,
        call_wall=None,
        put_wall=None,
        signals=[],
        best_contract=None,
        safer_contract=None,
        convex_contract=None,
        contract_warnings=[],
        dealer_distances={},
        bar_count=0,
        ema_stack="mixed",
        trend="neutral",
        rvol=1.0,
        compressed=False,
        ranked_at=utc_now_iso(),
        errors=[],
    )


def rank_symbol(
    symbol: str,
    narrative_tags: list[dict[str, Any]] | None = None,
    dte_min: int = DEFAULT_DTE_MIN,
    dte_max: int = DEFAULT_DTE_MAX,
    budget: float | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> SymbolRanking:
    symbol = symbol.upper()
    result = _default_ranking(symbol)
    conn: sqlite3.Connection | None = None

    try:
        conn = _open_connection()
        chain_rows = query_latest_chain(conn, symbol)
        if not chain_rows:
            result.errors.append(f"no_chain_data:{symbol}")
            return result

        spot = as_float(chain_rows[0].get("underlying_price"))
        dealer = compute_levels(chain_rows, spot=spot, symbol=symbol)

        bars = _load_underlying_bars(conn, symbol)
        technical_features, technical_state = build_technical_features(bars, "D")

        signals = evaluate_symbol_snapshot(
            symbol=symbol,
            dealer_features=dealer,
            technical_features=technical_features,
            technical_state=technical_state,
            narrative_tags=narrative_tags or [],
            min_confidence=min_confidence,
        )
        serialized_signals = [serialize_signal(s) for s in signals]

        primary_bias = signals[0].bias if signals else "bearish"
        contract_rec = score_and_rank_contracts(
            conn,
            symbol=symbol,
            bias=primary_bias,
            spot=spot,
            dte_min=dte_min,
            dte_max=dte_max,
            budget=budget,
        )

        result = SymbolRanking(
            symbol=symbol,
            spot=spot,
            regime=str(dealer.get("regime") or "neutral"),
            king_node=as_float(dealer.get("king_node")),
            call_wall=dealer.get("call_wall"),
            put_wall=dealer.get("put_wall"),
            signals=serialized_signals,
            best_contract=serialize_contract(contract_rec.best),
            safer_contract=serialize_contract(contract_rec.safer),
            convex_contract=serialize_contract(contract_rec.convex),
            contract_warnings=list(contract_rec.warnings),
            dealer_distances=dict(dealer.get("distances") or {}),
            bar_count=len(bars),
            ema_stack=technical_state.ema_stack,
            trend=technical_state.trend,
            rvol=technical_features.rvol,
            compressed=technical_state.compressed,
            ranked_at=utc_now_iso(),
            errors=[],
        )

        logger.info(
            "rank_symbol symbol=%s signals=%s contracts=%s regime=%s",
            symbol,
            len(serialized_signals),
            len(contract_rec.all_scored),
            result.regime,
        )
        return result
    except Exception as exc:  # pragma: no cover
        logger.exception("rank_symbol failure for %s", symbol)
        result.errors.append(str(exc))
        return result
    finally:
        if conn is not None:
            conn.close()


def rank_universe(
    symbols: list[str] | None = None,
    narrative_tags_by_symbol: dict[str, list[dict[str, Any]]] | None = None,
    dte_min: int = DEFAULT_DTE_MIN,
    dte_max: int = DEFAULT_DTE_MAX,
    budget: float | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> list[SymbolRanking]:
    watchlist = [s.upper() for s in (symbols or settings.watchlist)]
    tags_lookup = narrative_tags_by_symbol or {}

    rankings = [
        rank_symbol(
            symbol=s,
            narrative_tags=tags_lookup.get(s),
            dte_min=dte_min,
            dte_max=dte_max,
            budget=budget,
            min_confidence=min_confidence,
        )
        for s in watchlist
    ]

    def _highest_conf(r: SymbolRanking) -> float:
        if not r.signals:
            return -1.0
        return max(as_float(sig.get("confidence_raw")) for sig in r.signals)

    rankings.sort(key=lambda r: (1 if r.signals else 0, _highest_conf(r)), reverse=True)
    return rankings
