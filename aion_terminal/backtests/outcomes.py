# NOTE: aion_terminal/backtests/__init__.py should exist as an empty file.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import sqlite3
from typing import Any

from aion_terminal.features.contracts import ContractScore, score_and_rank_contracts
from aion_terminal.models.dto import SetupCandidateRecord, SetupOutcomeRecord
from aion_terminal.storage import repositories
from aion_terminal.utils.math_utils import as_float, as_int
from aion_terminal.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)

DEFAULT_DELTA_PROXY = 0.50
METHOD_DELTA_PROXY = "delta_proxy"
METHOD_ACTUAL_CANDLES = "actual_candles"


@dataclass(slots=True)
class OutcomeResult:
    candidate_id: str
    symbol: str
    setup_class: str
    bias: str
    contract_symbol: str | None
    moneyness_bucket: str | None
    entry_price: float
    entry_delta: float
    underlying_entry: float
    horizon_days: int
    underlying_exit: float
    underlying_return_pct: float
    estimated_option_return_pct: float
    max_favorable_excursion: float
    max_adverse_excursion: float
    hit_25: bool
    hit_50: bool
    hit_100: bool
    is_approximate: bool
    method: str


def estimate_option_return_delta_proxy(underlying_return_pct: float, delta: float, bias: str) -> float:
    """Estimate option return with approximate_delta_proxy method."""
    d = abs(delta)
    if d <= 0.0:
        d = DEFAULT_DELTA_PROXY

    adjusted_underlying = underlying_return_pct
    if bias.lower() == "bearish":
        adjusted_underlying = -underlying_return_pct

    leverage_factor = 1.0 / d
    return adjusted_underlying * d * leverage_factor


def compute_excursions(
    bars: list[dict[str, Any]],
    entry_price: float,
    bias: str,
    horizon_days: int,
) -> tuple[float, float]:
    """Compute favorable/adverse excursions in underlying pct terms over horizon."""
    if entry_price <= 0.0 or not bars:
        return 0.0, 0.0

    window = bars[:horizon_days]
    highs = [as_float(b.get("high")) for b in window]
    lows = [as_float(b.get("low")) for b in window]

    if bias.lower() == "bullish":
        favorable = (max(highs) - entry_price) / entry_price * 100.0
        adverse = (entry_price - min(lows)) / entry_price * 100.0
    else:
        favorable = (entry_price - min(lows)) / entry_price * 100.0
        adverse = (max(highs) - entry_price) / entry_price * 100.0

    return max(0.0, favorable), max(0.0, adverse)


def _load_bars_after(conn: sqlite3.Connection, symbol: str, as_of_ts: str, horizon_days: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT bar_ts, open, high, low, close, volume, vwap
        FROM underlying_bars
        WHERE symbol = ? AND bar_ts >= ?
        ORDER BY bar_ts ASC
        LIMIT ?
        """,
        (symbol, as_of_ts, horizon_days + 10),
    ).fetchall()
    return [dict(r) for r in rows]


def evaluate_candidate_outcome(
    conn: sqlite3.Connection,
    candidate: SetupCandidateRecord,
    best_contract: ContractScore | None,
    horizon_days: int = 5,
) -> OutcomeResult | None:
    """Evaluate one candidate using approximate_delta_proxy method."""
    bars = _load_bars_after(conn, candidate.symbol, candidate.as_of_ts, horizon_days)
    if len(bars) < horizon_days:
        return None

    entry = bars[0]
    exit_bar = bars[horizon_days - 1]
    entry_underlying = as_float(entry.get("close"))
    exit_underlying = as_float(exit_bar.get("close"))
    if entry_underlying <= 0.0:
        return None

    underlying_return_pct = (exit_underlying - entry_underlying) / entry_underlying * 100.0
    delta = abs(best_contract.delta) if best_contract is not None else DEFAULT_DELTA_PROXY
    estimated_option_return_pct = estimate_option_return_delta_proxy(underlying_return_pct, delta, candidate.direction or "bullish")

    mfe, mae = compute_excursions(bars, entry_underlying, candidate.direction or "bullish", horizon_days)

    entry_price = best_contract.premium_mid if best_contract is not None else 1.0

    return OutcomeResult(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        setup_class=candidate.setup_class,
        bias=candidate.direction or "",
        contract_symbol=best_contract.contract_symbol if best_contract is not None else None,
        moneyness_bucket=best_contract.moneyness_bucket if best_contract is not None else None,
        entry_price=entry_price,
        entry_delta=delta,
        underlying_entry=entry_underlying,
        horizon_days=horizon_days,
        underlying_exit=exit_underlying,
        underlying_return_pct=underlying_return_pct,
        estimated_option_return_pct=estimated_option_return_pct,
        max_favorable_excursion=mfe,
        max_adverse_excursion=mae,
        hit_25=estimated_option_return_pct >= 25.0,
        hit_50=estimated_option_return_pct >= 50.0,
        hit_100=estimated_option_return_pct >= 100.0,
        is_approximate=True,
        method=METHOD_DELTA_PROXY,
    )


def query_expectancy_by_setup_class(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.setup_class,
               AVG(o.pnl_pct) AS avg_pnl_pct,
               AVG(CASE WHEN o.is_winner = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
               COUNT(*) AS sample_count
        FROM setup_outcomes o
        JOIN setup_candidates c ON c.candidate_id = o.candidate_id
        GROUP BY c.setup_class
        ORDER BY sample_count DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def query_expectancy_by_symbol(conn: sqlite3.Connection, symbol: str | None = None) -> list[dict[str, Any]]:
    if symbol:
        rows = conn.execute(
            """
            SELECT c.symbol,
                   AVG(o.pnl_pct) AS avg_pnl_pct,
                   AVG(CASE WHEN o.is_winner = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
                   COUNT(*) AS sample_count
            FROM setup_outcomes o
            JOIN setup_candidates c ON c.candidate_id = o.candidate_id
            WHERE c.symbol = ?
            GROUP BY c.symbol
            """,
            (symbol,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT c.symbol,
                   AVG(o.pnl_pct) AS avg_pnl_pct,
                   AVG(CASE WHEN o.is_winner = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
                   COUNT(*) AS sample_count
            FROM setup_outcomes o
            JOIN setup_candidates c ON c.candidate_id = o.candidate_id
            GROUP BY c.symbol
            """
        ).fetchall()
    return [dict(r) for r in rows]


def query_expectancy_by_bias(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.direction,
               AVG(o.pnl_pct) AS avg_pnl_pct,
               AVG(CASE WHEN o.is_winner = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
               COUNT(*) AS sample_count
        FROM setup_outcomes o
        JOIN setup_candidates c ON c.candidate_id = o.candidate_id
        GROUP BY c.direction
        """
    ).fetchall()
    return [dict(r) for r in rows]


def query_expectancy_by_moneyness(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.outcome_label AS moneyness_bucket,
               AVG(o.pnl_pct) AS avg_pnl_pct,
               AVG(CASE WHEN o.is_winner = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
               COUNT(*) AS sample_count
        FROM setup_outcomes o
        WHERE o.outcome_label LIKE '%ITM%' OR o.outcome_label LIKE '%ATM%' OR o.outcome_label LIKE '%OTM%'
        GROUP BY o.outcome_label
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _to_setup_outcome_row(result: OutcomeResult) -> SetupOutcomeRecord:
    return SetupOutcomeRecord(
        candidate_id=result.candidate_id,
        outcome_ts=utc_now_iso(),
        pnl_abs=result.estimated_option_return_pct,
        pnl_pct=result.estimated_option_return_pct,
        max_favorable_excursion=result.max_favorable_excursion,
        max_adverse_excursion=result.max_adverse_excursion,
        hold_minutes=result.horizon_days * 24 * 60,
        is_winner=1 if result.estimated_option_return_pct > 0 else 0,
        outcome_label=(result.moneyness_bucket or "UNKNOWN") + "|approximate_delta_proxy",
        notes="approximate_delta_proxy based on underlying movement",
    )


def run_backtest_for_universe(
    conn: sqlite3.Connection,
    symbols: list[str],
    horizon_days: int = 5,
) -> list[OutcomeResult]:
    """Run batch backtest with approximate_delta_proxy method."""
    now = datetime.now(timezone.utc)
    from_ts = (now - timedelta(days=30)).isoformat()

    results: list[OutcomeResult] = []
    for symbol in symbols:
        candidates = repositories.query_ranked_candidates_by_date_range(
            conn,
            start_ts=from_ts,
            end_ts=now.isoformat(),
            symbol=symbol,
            setup_class=None,
            limit=200,
        )
        for candidate in candidates:
            rec = score_and_rank_contracts(
                conn,
                symbol=candidate.symbol,
                bias=(candidate.direction or "bullish"),
                spot=as_float(candidate.strike, default=0.0) or 100.0,
            )
            outcome = evaluate_candidate_outcome(conn, candidate, rec.best, horizon_days=horizon_days)
            if outcome is not None:
                results.append(outcome)

    if results:
        repositories.insert_setup_outcomes(conn, [_to_setup_outcome_row(r) for r in results])

    logger.info(
        "Backtest complete (approximate_delta_proxy): symbols=%s outcomes=%s horizon_days=%s",
        len(symbols),
        len(results),
        horizon_days,
    )
    return results
