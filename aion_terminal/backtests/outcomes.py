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
METHOD_SYNTHETIC_GREEKS = "synthetic_greeks"
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
    option_entry_price: float = 0.0
    option_exit_price_est: float = 0.0
    option_mfe_pct: float = 0.0
    option_mae_pct: float = 0.0
    theta_decay_est: float = 0.0
    gamma_pnl_est: float = 0.0
    delta_pnl_est: float = 0.0
    iv_pnl_est: float = 0.0
    stop_rule_hit: bool = False
    target_hit_level: str | None = None


def estimate_option_path_synthetic(
    bars: list[dict[str, Any]],
    entry_underlying: float,
    entry_option_price: float,
    bias: str,
    delta: float,
    gamma: float,
    theta: float,
    vega: float,
    iv_entry: float | None,
    horizon_days: int,
) -> dict[str, Any]:
    if not bars or entry_underlying <= 0.0:
        return {"exit_price": entry_option_price, "return_pct": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0, "delta_pnl_est": 0.0, "gamma_pnl_est": 0.0, "theta_decay_est": 0.0, "iv_pnl_est": 0.0, "path": []}

    d = abs(delta) if abs(delta) > 0 else DEFAULT_DELTA_PROXY
    g = gamma if gamma is not None else 0.0
    t = theta if theta is not None else 0.0
    v = vega if vega is not None else 0.0

    direction = -1.0 if bias.lower() == "bearish" else 1.0
    window = bars[:horizon_days]
    path: list[dict[str, Any]] = []
    exit_price = entry_option_price
    delta_pnl_last = 0.0
    gamma_pnl_last = 0.0
    theta_decay_last = 0.0
    iv_pnl_last = 0.0
    for i, bar in enumerate(window):
        close = as_float(bar.get("close"))
        underlying_move = close - entry_underlying
        directional_move = underlying_move * direction
        delta_pnl = directional_move * d
        gamma_pnl = 0.5 * g * (underlying_move**2)
        elapsed_days = i
        theta_decay = abs(t) * elapsed_days
        iv_proxy = as_float(bar.get("iv"), default=iv_entry if iv_entry is not None else 0.0)
        iv_change = iv_proxy - iv_entry if iv_entry is not None else 0.0
        iv_pnl = v * iv_change if iv_entry is not None else 0.0
        est_price = max(0.01, entry_option_price + delta_pnl + gamma_pnl - theta_decay + iv_pnl)
        path.append({"ts": bar.get("bar_ts"), "underlying_close": close, "option_price_est": est_price, "return_pct": ((est_price - entry_option_price) / entry_option_price * 100.0) if entry_option_price > 0 else 0.0})
        exit_price = est_price
        delta_pnl_last, gamma_pnl_last, theta_decay_last, iv_pnl_last = delta_pnl, gamma_pnl, theta_decay, iv_pnl

    rets = [p["return_pct"] for p in path] or [0.0]
    return {
        "exit_price": exit_price,
        "return_pct": ((exit_price - entry_option_price) / entry_option_price * 100.0) if entry_option_price > 0 else 0.0,
        "mfe_pct": max(rets),
        "mae_pct": min(rets),
        "delta_pnl_est": delta_pnl_last,
        "gamma_pnl_est": gamma_pnl_last,
        "theta_decay_est": theta_decay_last,
        "iv_pnl_est": iv_pnl_last,
        "path": path,
    }


def evaluate_option_targets_and_stops(
    option_path: list[dict[str, Any]],
    stop_loss_pct: float = -30.0,
    targets: list[float] | None = None,
) -> dict[str, Any]:
    target_levels = sorted(targets or [25.0, 50.0, 100.0])
    hit = {int(t): False for t in target_levels}
    first_target_ts = None
    stop_hit_ts = None
    target_hit_level = "none"
    stop_rule_hit = False
    for point in option_path:
        ret = as_float(point.get("return_pct"))
        ts = point.get("ts")
        hit_targets = [t for t in target_levels if ret >= t]
        if hit_targets and first_target_ts is None:
            target_hit_level = str(int(max(hit_targets)))
            first_target_ts = ts
            for t in target_levels:
                if ret >= t:
                    hit[int(t)] = True
            break
        if ret <= stop_loss_pct:
            stop_rule_hit = True
            stop_hit_ts = ts
            break
    if first_target_ts is None:
        for point in option_path:
            ret = as_float(point.get("return_pct"))
            for t in target_levels:
                if ret >= t:
                    hit[int(t)] = True
    if stop_hit_ts is None and first_target_ts is None:
        for point in option_path:
            if as_float(point.get("return_pct")) <= stop_loss_pct:
                stop_rule_hit = True
                stop_hit_ts = point.get("ts")
                break
    return {
        "hit_25": hit.get(25, False),
        "hit_50": hit.get(50, False),
        "hit_100": hit.get(100, False),
        "stop_rule_hit": stop_rule_hit,
        "target_hit_level": target_hit_level,
        "first_target_ts": first_target_ts,
        "stop_hit_ts": stop_hit_ts,
    }


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
    method: str = METHOD_SYNTHETIC_GREEKS,
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
    entry_price = best_contract.premium_mid if best_contract is not None else 1.0
    mfe, mae = compute_excursions(bars, entry_underlying, candidate.direction or "bullish", horizon_days)
    estimated_option_return_pct = 0.0
    method_used = method
    is_approximate = True
    option_exit_price_est = entry_price
    option_mfe_pct = 0.0
    option_mae_pct = 0.0
    delta_pnl_est = gamma_pnl_est = theta_decay_est = iv_pnl_est = 0.0
    stop_rule_hit = False
    target_hit_level = "none"
    hit_25 = hit_50 = hit_100 = False
    if method == METHOD_DELTA_PROXY:
        estimated_option_return_pct = estimate_option_return_delta_proxy(underlying_return_pct, delta, candidate.direction or "bullish")
        hit_25, hit_50, hit_100 = estimated_option_return_pct >= 25.0, estimated_option_return_pct >= 50.0, estimated_option_return_pct >= 100.0
        option_exit_price_est = max(0.01, entry_price * (1.0 + estimated_option_return_pct / 100.0))
        target_hit_level = "100" if hit_100 else "50" if hit_50 else "25" if hit_25 else "none"
    else:
        if method == METHOD_ACTUAL_CANDLES:
            method_used = METHOD_SYNTHETIC_GREEKS
            is_approximate = True
        syn = estimate_option_path_synthetic(
            bars=bars,
            entry_underlying=entry_underlying,
            entry_option_price=entry_price,
            bias=candidate.direction or "bullish",
            delta=delta,
            gamma=(best_contract.gamma if best_contract is not None else 0.0),
            theta=(best_contract.theta if best_contract is not None else 0.0),
            vega=0.0,
            iv_entry=(best_contract.iv if best_contract is not None else None),
            horizon_days=horizon_days,
        )
        estimated_option_return_pct = syn["return_pct"]
        option_exit_price_est = syn["exit_price"]
        option_mfe_pct = syn["mfe_pct"]
        option_mae_pct = syn["mae_pct"]
        delta_pnl_est = syn["delta_pnl_est"]
        gamma_pnl_est = syn["gamma_pnl_est"]
        theta_decay_est = syn["theta_decay_est"]
        iv_pnl_est = syn["iv_pnl_est"]
        eval_res = evaluate_option_targets_and_stops(syn["path"])
        hit_25, hit_50, hit_100 = eval_res["hit_25"], eval_res["hit_50"], eval_res["hit_100"]
        stop_rule_hit = eval_res["stop_rule_hit"]
        target_hit_level = eval_res["target_hit_level"]

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
        max_favorable_excursion=option_mfe_pct if method_used != METHOD_DELTA_PROXY else mfe,
        max_adverse_excursion=abs(option_mae_pct) if method_used != METHOD_DELTA_PROXY else mae,
        hit_25=hit_25,
        hit_50=hit_50,
        hit_100=hit_100,
        is_approximate=is_approximate,
        method=method_used,
        option_entry_price=entry_price,
        option_exit_price_est=option_exit_price_est,
        option_mfe_pct=option_mfe_pct,
        option_mae_pct=option_mae_pct,
        theta_decay_est=theta_decay_est,
        gamma_pnl_est=gamma_pnl_est,
        delta_pnl_est=delta_pnl_est,
        iv_pnl_est=iv_pnl_est,
        stop_rule_hit=stop_rule_hit,
        target_hit_level=target_hit_level,
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
        outcome_label=f"{result.moneyness_bucket or 'UNKNOWN'}|{result.method}|{result.target_hit_level or 'none'}",
        notes=(
            f"theta_decay_est={result.theta_decay_est:.4f};gamma_pnl_est={result.gamma_pnl_est:.4f};"
            f"delta_pnl_est={result.delta_pnl_est:.4f};iv_pnl_est={result.iv_pnl_est:.4f};"
            f"stop_rule_hit={result.stop_rule_hit}"
        ),
    )


def run_backtest_for_universe(
    conn: sqlite3.Connection,
    symbols: list[str],
    horizon_days: int = 5,
    method: str = METHOD_SYNTHETIC_GREEKS,
) -> list[OutcomeResult]:
    """Run batch backtest with approximate_delta_proxy method."""
    now = datetime.now(timezone.utc)
    from_ts = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

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
            bars = _load_bars_after(conn, candidate.symbol, candidate.as_of_ts, horizon_days)
            spot = as_float((bars[0] if bars else {}).get("close"), default=0.0)
            if spot <= 0:
                spot = as_float(getattr(candidate, "underlying_price", None), default=0.0) or as_float(getattr(candidate, "spot", None), default=0.0)
            if spot <= 0:
                spot = as_float(candidate.strike, default=0.0) or 100.0
                logger.warning("Using strike as fallback spot for candidate_id=%s symbol=%s", candidate.candidate_id, candidate.symbol)
            rec = score_and_rank_contracts(
                conn,
                symbol=candidate.symbol,
                bias=(candidate.direction or "bullish"),
                spot=spot,
            )
            outcome = evaluate_candidate_outcome(conn, candidate, rec.best, horizon_days=horizon_days, method=method)
            if outcome is not None:
                results.append(outcome)

    if results:
        repositories.insert_setup_outcomes(conn, [_to_setup_outcome_row(r) for r in results])

    logger.info(
        "Backtest complete: method=%s symbols=%s outcomes=%s horizon_days=%s",
        method,
        len(symbols),
        len(results),
        horizon_days,
    )
    return results


def query_expectancy_by_method(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT substr(o.outcome_label, instr(o.outcome_label, '|') + 1,
                      instr(substr(o.outcome_label, instr(o.outcome_label, '|') + 1), '|') - 1) AS method,
               AVG(o.pnl_pct) AS avg_pnl_pct, COUNT(*) AS sample_count
        FROM setup_outcomes o
        GROUP BY method
        """
    ).fetchall()
    return [dict(r) for r in rows]


def query_expectancy_by_horizon(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT (o.hold_minutes / 1440) AS horizon_days,
               AVG(o.pnl_pct) AS avg_pnl_pct,
               AVG(CASE WHEN o.is_winner = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
               COUNT(*) AS sample_count
        FROM setup_outcomes o
        GROUP BY (o.hold_minutes / 1440)
        ORDER BY horizon_days
        """
    ).fetchall()
    return [dict(r) for r in rows]


def query_expectancy_by_setup_and_moneyness(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.setup_class,
               substr(o.outcome_label, 1, instr(o.outcome_label, '|') - 1) AS moneyness_bucket,
               AVG(o.pnl_pct) AS avg_pnl_pct,
               COUNT(*) AS sample_count
        FROM setup_outcomes o
        JOIN setup_candidates c ON c.candidate_id = o.candidate_id
        GROUP BY c.setup_class, moneyness_bucket
        ORDER BY sample_count DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]
