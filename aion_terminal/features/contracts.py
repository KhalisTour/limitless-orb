from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import sqlite3
from typing import Any

from aion_terminal.utils.math_utils import as_float, as_int

logger = logging.getLogger(__name__)

TARGET_DTE_MIN = 1
TARGET_DTE_MAX = 30
ATM_BAND_PCT = 2.0
ITM_DEPTH_PCT = 5.0
OTM_DEPTH_PCT = 5.0
MIN_OPEN_INTEREST = 10
MIN_VOLUME = 0
MIN_DELTA_CALL = 0.10
MAX_DELTA_CALL = 0.90
LIQUIDITY_SPREAD_MAX_PCT = 0.30
DEFAULT_HOLD_DAYS = 5

DELTA_PER_DOLLAR_MAX = 2.0
GAMMA_PER_DOLLAR_MAX = 0.1

WEIGHT_DELTA_PER_DOLLAR = 0.20
WEIGHT_GAMMA_PER_DOLLAR = 0.15
WEIGHT_LIQUIDITY = 0.25
WEIGHT_OI_CLUSTER = 0.15
WEIGHT_EXPECTED_MOVE = 0.25


@dataclass(slots=True)
class ContractScore:
    contract_symbol: str
    expiry: str
    strike: float
    side: str
    dte: int
    moneyness_bucket: str
    premium_mid: float
    delta: float
    gamma: float
    theta: float
    iv: float
    open_interest: int
    volume: int
    bid: float
    ask: float
    delta_per_dollar: float
    gamma_per_dollar: float
    theta_burden: float
    spread_pct: float
    liquidity_score: float
    oi_cluster_score: float
    expected_move_fit: float
    total_score: float
    size_recommendation: int | None


@dataclass(slots=True)
class ContractRecommendation:
    symbol: str
    bias: str
    spot: float
    best: ContractScore | None
    safer: ContractScore | None
    convex: ContractScore | None
    all_scored: list[ContractScore]
    warnings: list[str]


def classify_moneyness(strike: float, spot: float, side: str) -> str:
    """Classify moneyness bucket relative to spot and contract side."""
    if spot <= 0.0:
        return "ATM"

    lower_atm = spot * (1 - ATM_BAND_PCT / 100.0)
    upper_atm = spot * (1 + ATM_BAND_PCT / 100.0)

    if side == "call":
        if strike < lower_atm:
            return "ITM"
        if strike > upper_atm:
            return "OTM"
        return "ATM"

    if strike > upper_atm:
        return "ITM"
    if strike < lower_atm:
        return "OTM"
    return "ATM"


def compute_liquidity_score(bid: float, ask: float, mid: float, open_interest: int, volume: int) -> float:
    """Compute spread/OI blended liquidity score in [0, 1]."""
    del volume  # volume reserved for future expansion
    spread_ratio = ((ask - bid) / mid) if mid > 0.0 else 1.0
    spread_score = max(0.0, 1.0 - spread_ratio / LIQUIDITY_SPREAD_MAX_PCT)
    oi_score = min(1.0, open_interest / 500.0)
    return (spread_score * 0.6) + (oi_score * 0.4)


def compute_oi_cluster_score(strike: float, all_strikes_oi: list[tuple[float, int]]) -> float:
    """Score how close the strike is to largest OI cluster strike."""
    if not all_strikes_oi:
        return 0.0

    cluster_strike, _ = max(all_strikes_oi, key=lambda item: item[1])
    if cluster_strike <= 0.0:
        return 0.0
    if strike == cluster_strike:
        return 1.0

    return max(0.0, 1.0 - abs(strike - cluster_strike) / cluster_strike * 20.0)


def compute_expected_move_fit(strike: float, spot: float, iv: float, dte: int, side: str) -> float:
    """Score strike fit to one-sigma expected move target in [0, 1]."""
    if spot <= 0.0 or iv <= 0.0 or dte <= 0:
        return 0.0

    expected_move = spot * iv * math.sqrt(dte / 365.0)
    if expected_move <= 0.0:
        return 0.0

    target = (spot + expected_move) if side == "call" else (spot - expected_move)
    fit = 1.0 - abs(strike - target) / expected_move
    return max(0.0, min(1.0, fit))


def _normalize_component(value: float, upper: float) -> float:
    if upper <= 0.0:
        return 0.0
    return max(0.0, min(1.0, value / upper))


def score_contract(
    contract: dict[str, Any],
    spot: float,
    side: str,
    all_strikes_oi: list[tuple[float, int]],
    budget: float | None = None,
) -> ContractScore | None:
    """Score one chain contract row. Returns None when base quality filters fail."""
    contract_side = str(contract.get("side") or contract.get("type") or "").lower()
    if contract_side != side:
        return None

    bid = as_float(contract.get("bid"))
    ask = as_float(contract.get("ask"))
    mid = as_float(contract.get("mark"))
    if mid <= 0.0:
        mid = (bid + ask) / 2.0 if (bid > 0.0 and ask > 0.0) else 0.0

    oi = as_int(contract.get("open_interest"))
    volume = as_int(contract.get("volume"))
    strike = as_float(contract.get("strike"))
    delta = as_float(contract.get("delta"))
    gamma = as_float(contract.get("gamma"))
    theta = as_float(contract.get("theta"))
    iv = as_float(contract.get("iv"))
    dte = as_int(contract.get("dte"))

    if oi < MIN_OPEN_INTEREST:
        return None
    if volume < MIN_VOLUME:
        return None
    if mid <= 0.0:
        return None

    spread_ratio = (ask - bid) / mid if mid > 0.0 else 1.0
    if spread_ratio > LIQUIDITY_SPREAD_MAX_PCT * 2.0:
        return None

    if side == "call" and not (MIN_DELTA_CALL <= delta <= MAX_DELTA_CALL):
        return None
    if side == "put" and not (-MAX_DELTA_CALL <= delta <= -MIN_DELTA_CALL):
        return None

    delta_per_dollar = abs(delta) / mid if mid > 0.0 else 0.0
    gamma_per_dollar = gamma / mid if mid > 0.0 else 0.0
    theta_burden = abs(theta) * DEFAULT_HOLD_DAYS / mid if mid > 0.0 else 0.0
    spread_pct = spread_ratio * 100.0
    liquidity_score = compute_liquidity_score(bid, ask, mid, oi, volume)
    oi_cluster_score = compute_oi_cluster_score(strike, all_strikes_oi)
    expected_move_fit = compute_expected_move_fit(strike, spot, iv, dte, side)

    total_score = (
        _normalize_component(delta_per_dollar, DELTA_PER_DOLLAR_MAX) * WEIGHT_DELTA_PER_DOLLAR
        + _normalize_component(gamma_per_dollar, GAMMA_PER_DOLLAR_MAX) * WEIGHT_GAMMA_PER_DOLLAR
        + liquidity_score * WEIGHT_LIQUIDITY
        + oi_cluster_score * WEIGHT_OI_CLUSTER
        + expected_move_fit * WEIGHT_EXPECTED_MOVE
    )

    size_recommendation = None
    if budget is not None and budget > 0.0:
        size_recommendation = int(budget / (mid * 100.0))

    return ContractScore(
        contract_symbol=str(contract.get("option_symbol") or contract.get("contract_symbol") or ""),
        expiry=str(contract.get("expiry") or ""),
        strike=strike,
        side=side,
        dte=dte,
        moneyness_bucket=classify_moneyness(strike, spot, side),
        premium_mid=mid,
        delta=delta,
        gamma=gamma,
        theta=theta,
        iv=iv,
        open_interest=oi,
        volume=volume,
        bid=bid,
        ask=ask,
        delta_per_dollar=delta_per_dollar,
        gamma_per_dollar=gamma_per_dollar,
        theta_burden=theta_burden,
        spread_pct=spread_pct,
        liquidity_score=liquidity_score,
        oi_cluster_score=oi_cluster_score,
        expected_move_fit=expected_move_fit,
        total_score=max(0.0, min(1.0, total_score)),
        size_recommendation=size_recommendation,
    )


def load_chain_for_scoring(
    conn: sqlite3.Connection,
    symbol: str,
    bias: str,
    dte_min: int = TARGET_DTE_MIN,
    dte_max: int = TARGET_DTE_MAX,
) -> list[dict[str, Any]]:
    """Load latest rows per option symbol from raw_chain_snapshots for scoring."""
    side = "call" if bias.lower() == "bullish" else "put"
    rows = conn.execute(
        """
        SELECT r.option_symbol, r.expiry, r.side, r.strike, r.bid, r.ask, r.mark,
               r.iv, r.delta, r.gamma, r.theta, r.open_interest, r.volume,
               r.dte, r.underlying_price, r.snapshot_ts
        FROM raw_chain_snapshots r
        INNER JOIN (
            SELECT option_symbol, MAX(snapshot_ts) AS max_ts
            FROM raw_chain_snapshots
            WHERE symbol = ?
            GROUP BY option_symbol
        ) latest
            ON r.option_symbol = latest.option_symbol AND r.snapshot_ts = latest.max_ts
        WHERE r.symbol = ?
          AND LOWER(r.side) = ?
          AND r.dte BETWEEN ? AND ?
        ORDER BY r.expiry, r.strike
        """,
        (symbol, symbol, side, dte_min, dte_max),
    ).fetchall()
    return [dict(row) for row in rows]


def score_and_rank_contracts(
    conn: sqlite3.Connection,
    symbol: str,
    bias: str,
    spot: float,
    dte_min: int = TARGET_DTE_MIN,
    dte_max: int = TARGET_DTE_MAX,
    budget: float | None = None,
) -> ContractRecommendation:
    """Score and rank contracts for a symbol/bias from stored snapshots."""
    side = "call" if bias.lower() == "bullish" else "put"
    rows = load_chain_for_scoring(conn, symbol, bias, dte_min=dte_min, dte_max=dte_max)
    if not rows:
        logger.warning("No contracts available for scoring symbol=%s bias=%s", symbol, bias)

    all_strikes_oi = [(as_float(r.get("strike")), as_int(r.get("open_interest"))) for r in rows]
    scored: list[ContractScore] = []
    for row in rows:
        scored_row = score_contract(row, spot=spot, side=side, all_strikes_oi=all_strikes_oi, budget=budget)
        if scored_row is not None:
            scored.append(scored_row)

    scored.sort(key=lambda c: c.total_score, reverse=True)

    balanced_candidates = [
        c for c in scored
        if 0.35 <= abs(c.delta) <= 0.65 and c.spread_pct <= 10.0 and c.liquidity_score >= 0.4 and c.theta_burden <= 1.0 and c.moneyness_bucket in {"ATM", "ITM", "OTM"}
    ]
    if balanced_candidates:
        balanced_candidates = sorted(
            balanced_candidates,
            key=lambda c: (0 if c.moneyness_bucket == "ATM" else 1, abs(abs(c.delta) - 0.5), -c.liquidity_score, c.theta_burden),
        )
    best = balanced_candidates[0] if balanced_candidates else next((c for c in scored if c.liquidity_score > 0.3), None)

    safer_candidates = [
        c for c in scored
        if abs(c.delta) >= 0.55 and c.liquidity_score >= 0.45 and c.theta_burden <= 0.9 and c.moneyness_bucket in {"ITM", "ATM"}
    ]
    safer = safer_candidates[0] if safer_candidates else next((c for c in scored if c.moneyness_bucket in {"ITM", "ATM"}), None)

    convex_candidates = sorted(
        [c for c in scored if c.moneyness_bucket == "OTM" and c.liquidity_score >= 0.2],
        key=lambda c: (c.gamma_per_dollar + c.delta_per_dollar + c.expected_move_fit),
        reverse=True,
    )
    convex = convex_candidates[0] if convex_candidates else next((c for c in scored if c.moneyness_bucket == "OTM"), None)
    if best is not None and convex is not None and best.contract_symbol == convex.contract_symbol:
        alternate_best = next((c for c in balanced_candidates if c.contract_symbol != convex.contract_symbol), None)
        if alternate_best is not None:
            best = alternate_best

    warnings: list[str] = []
    if len(scored) < 5:
        warnings.append("fewer_than_5_contracts_scored")
    if safer is None:
        warnings.append("no_itm_contracts_found")
    if convex is None:
        warnings.append("no_otm_contracts_found")
    if best is not None and best.spread_pct > 0.15:
        warnings.append("best_contract_spread_wide")

    return ContractRecommendation(
        symbol=symbol,
        bias=bias,
        spot=spot,
        best=best,
        safer=safer,
        convex=convex,
        all_scored=scored,
        warnings=warnings,
    )
