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


@dataclass(slots=True)
class RankedItem:
    """Unified ranking output for frontend consumption."""
    symbol: str
    ranking_score: float
    setup_class: str
    bias: str
    regime: str
    confidence_bucket: str
    key_levels: dict[str, Any]
    technical_summary: dict[str, Any]
    top_contract: dict[str, Any]
    actionability: str
    has_trade_plan: bool
    warnings: list[str]

SCHEMA_PATH = "aion_terminal/storage/schema.sql"
DEFAULT_DTE_MIN = 0
DEFAULT_DTE_MAX = 21
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


def infer_bias(
    *,
    explicit_bias: str | None = None,
    setup_bias: str | None = None,
    ranking_bias: str | None = None,
    technical_state: TechnicalState | None = None,
    dealer_features: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    for candidate in (explicit_bias, setup_bias, ranking_bias):
        if candidate in {"bullish", "bearish"}:
            return str(candidate), []
    if technical_state is not None:
        if technical_state.ema_stack == "bullish_stack" or technical_state.trend in {"uptrend", "strong_uptrend"}:
            return "bullish", []
        if technical_state.ema_stack == "bearish_stack" or technical_state.trend in {"downtrend", "strong_downtrend"}:
            return "bearish", []
    dealer = dealer_features or {}
    if str(dealer.get("regime", "")).lower() in {"trend", "acceleration"} and as_float(dealer.get("put_wall")) <= as_float(dealer.get("spot")):
        return "bullish", []
    return "bullish", ["bias_defaulted"]


def _load_underlying_bars(conn: sqlite3.Connection, symbol: str) -> list[UnderlyingBarRecord]:
    rows = conn.execute(
        """
        SELECT symbol, timeframe, bar_ts, open, high, low, close, volume, vwap
        FROM underlying_bars
        WHERE symbol = ?
          AND timeframe = (
              SELECT CASE
                  WHEN EXISTS (SELECT 1 FROM underlying_bars WHERE symbol = ? AND timeframe = '1D') THEN '1D'
                  WHEN EXISTS (SELECT 1 FROM underlying_bars WHERE symbol = ? AND timeframe = 'daily') THEN 'daily'
                  ELSE 'D'
              END
          )
        ORDER BY bar_ts DESC
        LIMIT ?
        """,
        (symbol, symbol, symbol, BARS_LOOKBACK),
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
            result.errors.extend(["cache_miss", "refresh_required"])
            logger.info("ranking cache-only symbol=%s cache_hit=%s", symbol, False)
            return result
        logger.info("ranking cache-only symbol=%s cache_hit=%s", symbol, True)

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

        primary_bias = infer_bias(
            setup_bias=signals[0].bias if signals else None,
            technical_state=technical_state,
            dealer_features=dealer,
        )[0]
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

    if settings.cache_only:
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
    else:
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


def _to_ranked_item(ranking: SymbolRanking, degraded: bool = False) -> RankedItem:
    """Convert SymbolRanking to unified RankedItem response format."""
    signals = ranking.signals or []
    best_signal = signals[0] if signals else None
    setup_class = str(best_signal.get("setup_class", "")) if best_signal else "none"
    bias = str(best_signal.get("bias", "bullish")) if best_signal else "bullish"
    
    # Calculate ranking_score: average of signal confidences if available, else 0
    confidences = [as_float(s.get("confidence_raw", 0)) for s in signals]
    base_score = (sum(confidences) / len(confidences)) if confidences else 0.0
    ranking_score = min(1.0, max(0.0, base_score * (0.5 if degraded else 1.0)))  # Halve degraded scores
    
    confidence_bucket = "high" if ranking_score >= 0.7 else "medium" if ranking_score >= 0.4 else "low"
    
    key_levels = {
        "king_node": ranking.king_node,
        "call_wall": ranking.call_wall,
        "put_wall": ranking.put_wall,
    }
    
    technical_summary = {
        "ema_stack": ranking.ema_stack,
        "rvol": ranking.rvol,
        "trend": ranking.trend,
        "compressed": ranking.compressed,
        "bar_count": ranking.bar_count,
    }
    
    # Format top contract if available
    top_contract: dict[str, Any] = {}
    if ranking.best_contract:
        tc = ranking.best_contract
        top_contract = {
            "contract_symbol": tc.get("contract_symbol"),
            "expiry": tc.get("expiry"),
            "strike": tc.get("strike"),
            "premium_mid": tc.get("premium_mid"),
            "delta": tc.get("delta"),
            "gamma": tc.get("gamma"),
            "theta": tc.get("theta"),
            "open_interest": tc.get("open_interest"),
            "spread_pct": tc.get("spread_pct"),
            "contract_quality_score": tc.get("contract_quality_score"),
            "volatility_alignment_score": tc.get("volatility_alignment_score"),
        }
    
    warnings = ranking.contract_warnings + ranking.errors
    if not top_contract:
        warnings.append("missing_top_contract")
    if degraded:
        warnings.append("degraded_ranking: low confidence; check validation")

    has_valid_setup = setup_class != "none" and bool(signals)
    has_valid_contract = bool(top_contract)
    if not has_valid_setup:
        actionability = "low"
    elif has_valid_contract:
        actionability = "high"
    else:
        actionability = "medium"

    has_trade_plan = bool(ranking.symbol and ranking.symbol.strip())

    return RankedItem(
        symbol=ranking.symbol,
        ranking_score=ranking_score,
        setup_class=setup_class,
        bias=bias,
        regime=ranking.regime,
        confidence_bucket=confidence_bucket,
        key_levels=key_levels,
        technical_summary=technical_summary,
        top_contract=top_contract,
        actionability=actionability,
        has_trade_plan=has_trade_plan,
        warnings=warnings,
    )


def get_rankings_unified(
    symbols: list[str] | None = None,
    setup_class: str | None = None,
    bias: str | None = None,
    limit: int = 10,
    dte_min: int = DEFAULT_DTE_MIN,
    dte_max: int = DEFAULT_DTE_MAX,
    budget: float | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> tuple[list[RankedItem], list[str]]:
    """Get rankings in unified format with automatic degradation if needed.
    
    Returns: (items, warnings)
    - If strict thresholds yield enough results (>= limit/2), return those.
    - If fewer, provide degraded list using all symbols sorted by best available signal.
    - All items sorted by ranking_score desc, then by rvol desc.
    """
    rankings = rank_universe(
        symbols=symbols,
        dte_min=dte_min,
        dte_max=dte_max,
        budget=budget,
        min_confidence=min_confidence,
    )
    
    # Convert to unified format
    items = [_to_ranked_item(r) for r in rankings]
    
    # Apply filters
    if setup_class:
        items = [item for item in items if item.setup_class.lower() == setup_class.lower()]
    if bias:
        items = [item for item in items if item.bias.lower() == bias.lower()]
    
    warnings = []
    
    # Degradation: if fewer than limit/2, include all and mark degraded
    min_threshold = max(1, limit // 2)
    if len(items) < min_threshold and not settings.cache_only:
        # Get all symbols and rank by any signal confidence available
        all_rankings = rank_universe(
            symbols=symbols,
            dte_min=dte_min,
            dte_max=dte_max,
            budget=budget,
            min_confidence=0.0,  # Accept all
        )
        degraded_items = [_to_ranked_item(r, degraded=True) for r in all_rankings]
        
        # Reapply filters
        if setup_class:
            degraded_items = [item for item in degraded_items if item.setup_class.lower() == setup_class.lower()]
        if bias:
            degraded_items = [item for item in degraded_items if item.bias.lower() == bias.lower()]
        
        items = degraded_items
        warnings.append("Degraded rankings: insufficient strict-confidence candidates; showing best available")
    
    # Sort: ranking_score desc, contract_quality_score desc, volatility_alignment_score desc, then rvol.
    items.sort(
        key=lambda item: (
            -item.ranking_score,
            -as_float(item.top_contract.get("contract_quality_score", -1.0)),
            -as_float(item.top_contract.get("volatility_alignment_score", -1.0)),
            -as_float(item.technical_summary.get("rvol", 0.0)),
        )
    )
    
    return items[: max(1, limit)], warnings
