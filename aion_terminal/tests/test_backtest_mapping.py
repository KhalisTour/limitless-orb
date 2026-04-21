from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from aion_terminal.backtests.outcomes import (
    compute_excursions,
    estimate_option_return_delta_proxy,
    evaluate_candidate_outcome,
)
from aion_terminal.features.contracts import ContractScore
from aion_terminal.models.dto import SetupCandidateRecord
from aion_terminal.storage.db import bootstrap_schema, get_connection


def make_bars(n=20, start_price=100.0, trend="up"):
    rows = []
    price = start_price
    for i in range(n):
        if trend == "up":
            close = price * 1.005
        elif trend == "down":
            close = price * 0.995
        else:
            close = price
        row = {
            "bar_ts": f"2026-04-{i+1:02d}T00:00:00+00:00",
            "open": price,
            "high": max(price, close) * 1.002,
            "low": min(price, close) * 0.998,
            "close": close,
            "volume": 1000,
        }
        rows.append(row)
        price = close
    return rows


def _insert_underlying(conn, symbol: str, bars: list[dict]):
    conn.executemany(
        """
        INSERT INTO underlying_bars (
            symbol, timeframe, bar_ts, open, high, low, close, volume, vwap, source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                symbol,
                "1D",
                b["bar_ts"],
                b["open"],
                b["high"],
                b["low"],
                b["close"],
                b["volume"],
                b["close"],
                "test",
                b["bar_ts"],
                b["bar_ts"],
            )
            for b in bars
        ],
    )
    conn.commit()


def _sample_contract(delta: float = 0.5) -> ContractScore:
    return ContractScore(
        contract_symbol="O:SPY260417C00100000",
        expiry="2026-04-17",
        strike=100.0,
        side="call",
        dte=10,
        moneyness_bucket="ATM",
        premium_mid=2.0,
        delta=delta,
        gamma=0.05,
        theta=-0.02,
        iv=0.3,
        open_interest=500,
        volume=100,
        bid=1.9,
        ask=2.1,
        delta_per_dollar=0.2,
        gamma_per_dollar=0.02,
        theta_burden=0.05,
        spread_pct=10.0,
        liquidity_score=0.9,
        oi_cluster_score=0.8,
        expected_move_fit=0.8,
        total_score=0.8,
        size_recommendation=None,
    )


def test_estimate_option_return_bullish_up():
    assert estimate_option_return_delta_proxy(5.0, 0.50, "bullish") > 0


def test_estimate_option_return_bearish_down():
    assert estimate_option_return_delta_proxy(-5.0, 0.50, "bearish") > 0


def test_compute_excursions_bullish_uptrend():
    bars = make_bars(n=10, start_price=100.0, trend="up")
    fav, adv = compute_excursions(bars, 100.0, "bullish", 10)
    assert fav > 0
    assert adv < fav


def test_compute_excursions_bearish_downtrend():
    bars = make_bars(n=10, start_price=100.0, trend="down")
    fav, adv = compute_excursions(bars, 100.0, "bearish", 10)
    assert fav > 0
    assert adv < fav


def test_evaluate_candidate_outcome_returns_none_insufficient_bars(tmp_path):
    conn = get_connection(str(tmp_path / "outcomes.db"))
    schema_path = Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
    bootstrap_schema(conn, str(schema_path))

    _insert_underlying(conn, "SPY", make_bars(n=2, trend="up"))
    candidate = SetupCandidateRecord(
        candidate_id="c1",
        as_of_ts="2026-04-01T00:00:00+00:00",
        symbol="SPY",
        setup_class="momentum_continuation",
        score=0.7,
        direction="bullish",
    )
    assert evaluate_candidate_outcome(conn, candidate, _sample_contract(), horizon_days=5) is None
    conn.close()


def test_hit_thresholds_on_strong_move(tmp_path):
    conn = get_connection(str(tmp_path / "outcomes2.db"))
    schema_path = Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
    bootstrap_schema(conn, str(schema_path))

    bars = make_bars(n=10, start_price=100.0, trend="up")
    # force stronger move by widening last close
    bars[-1]["close"] = 160.0
    bars[-1]["high"] = 161.0
    _insert_underlying(conn, "SPY", bars)

    candidate = SetupCandidateRecord(
        candidate_id="c2",
        as_of_ts="2026-04-01T00:00:00+00:00",
        symbol="SPY",
        setup_class="momentum_continuation",
        score=0.8,
        direction="bullish",
    )
    outcome = evaluate_candidate_outcome(conn, candidate, _sample_contract(delta=0.5), horizon_days=10)
    assert outcome is not None
    assert outcome.hit_25 is True
    assert outcome.hit_50 is True
    conn.close()
