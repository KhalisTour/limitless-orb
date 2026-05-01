from __future__ import annotations

from pathlib import Path

from aion_terminal.backtests.outcomes import (
    METHOD_DELTA_PROXY,
    METHOD_SYNTHETIC_GREEKS,
    compute_excursions,
    estimate_option_path_synthetic,
    estimate_option_return_delta_proxy,
    evaluate_option_targets_and_stops,
    evaluate_candidate_outcome,
    query_expectancy_by_horizon,
    query_expectancy_by_method,
    query_expectancy_by_setup_and_moneyness,
    run_backtest_for_universe,
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
    outcome = evaluate_candidate_outcome(
        conn,
        candidate,
        _sample_contract(delta=0.5),
        horizon_days=10,
        method=METHOD_DELTA_PROXY,
    )
    assert outcome is not None
    assert outcome.hit_25 is True
    assert outcome.hit_50 is True
    conn.close()


def test_synthetic_call_rises_when_underlying_rises():
    bars = make_bars(n=5, start_price=100.0, trend="up")
    out = estimate_option_path_synthetic(bars, 100.0, 2.0, "bullish", 0.5, 0.0, 0.0, 0.0, None, 5)
    assert out["return_pct"] > 0


def test_synthetic_put_rises_when_underlying_falls():
    bars = make_bars(n=5, start_price=100.0, trend="down")
    out = estimate_option_path_synthetic(bars, 100.0, 2.0, "bearish", 0.5, 0.0, 0.0, 0.0, None, 5)
    assert out["return_pct"] > 0


def test_theta_decay_reduces_flat_underlying_value():
    bars = make_bars(n=5, start_price=100.0, trend="flat")
    out = estimate_option_path_synthetic(bars, 100.0, 2.0, "bullish", 0.5, 0.0, -0.1, 0.0, None, 5)
    assert out["exit_price"] < 2.0


def test_target_hit_before_stop():
    path = [
        {"ts": "2026-04-01T00:00:00+00:00", "return_pct": 10.0},
        {"ts": "2026-04-02T00:00:00+00:00", "return_pct": 30.0},
        {"ts": "2026-04-03T00:00:00+00:00", "return_pct": -35.0},
    ]
    res = evaluate_option_targets_and_stops(path)
    assert res["target_hit_level"] == "25"
    assert res["stop_rule_hit"] is False


def test_stop_hit_before_target():
    path = [
        {"ts": "2026-04-01T00:00:00+00:00", "return_pct": -35.0},
        {"ts": "2026-04-02T00:00:00+00:00", "return_pct": 30.0},
    ]
    res = evaluate_option_targets_and_stops(path)
    assert res["stop_rule_hit"] is True
    assert res["target_hit_level"] == "none"


def test_evaluate_candidate_outcome_synthetic_method(tmp_path):
    conn = get_connection(str(tmp_path / "outcomes3.db"))
    bootstrap_schema(conn, str(Path(__file__).resolve().parents[1] / "storage" / "schema.sql"))
    _insert_underlying(conn, "SPY", make_bars(n=10, trend="up"))
    candidate = SetupCandidateRecord(candidate_id="c3", as_of_ts="2026-04-01T00:00:00+00:00", symbol="SPY", setup_class="x", score=1.0, direction="bullish")
    out = evaluate_candidate_outcome(conn, candidate, _sample_contract(), horizon_days=5, method=METHOD_SYNTHETIC_GREEKS)
    assert out is not None
    assert out.estimated_option_return_pct != 0
    conn.close()


def test_delta_proxy_still_works(tmp_path):
    conn = get_connection(str(tmp_path / "outcomes4.db"))
    bootstrap_schema(conn, str(Path(__file__).resolve().parents[1] / "storage" / "schema.sql"))
    _insert_underlying(conn, "SPY", make_bars(n=10, trend="up"))
    candidate = SetupCandidateRecord(candidate_id="c4", as_of_ts="2026-04-01T00:00:00+00:00", symbol="SPY", setup_class="x", score=1.0, direction="bullish")
    out = evaluate_candidate_outcome(conn, candidate, _sample_contract(), horizon_days=5, method=METHOD_DELTA_PROXY)
    assert out is not None
    assert out.method == METHOD_DELTA_PROXY
    conn.close()


def test_spot_selection_uses_bar_close_not_strike(tmp_path, monkeypatch):
    conn = get_connection(str(tmp_path / "outcomes5.db"))
    bootstrap_schema(conn, str(Path(__file__).resolve().parents[1] / "storage" / "schema.sql"))
    _insert_underlying(conn, "SPY", make_bars(n=10, start_price=123.0, trend="flat"))
    candidate = SetupCandidateRecord(candidate_id="c5", as_of_ts="2026-04-01T00:00:00+00:00", symbol="SPY", setup_class="x", score=1.0, direction="bullish", strike=999.0)
    conn.execute(
        "INSERT INTO setup_candidates (candidate_id, as_of_ts, symbol, setup_class, direction, score, strike, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (candidate.candidate_id, candidate.as_of_ts, candidate.symbol, candidate.setup_class, candidate.direction, candidate.score, candidate.strike, candidate.as_of_ts, candidate.as_of_ts),
    )
    conn.commit()
    captured = {}

    def fake_score(conn, symbol, bias, spot):
        captured["spot"] = spot
        class Rec:
            best = _sample_contract()
        return Rec()

    monkeypatch.setattr("aion_terminal.backtests.outcomes.score_and_rank_contracts", fake_score)
    run_backtest_for_universe(conn, ["SPY"], 5)
    assert round(captured["spot"], 2) == round(make_bars(n=1, start_price=123.0, trend="flat")[0]["close"], 2)
    conn.close()


def test_query_helpers_structure(tmp_path):
    conn = get_connection(str(tmp_path / "outcomes6.db"))
    bootstrap_schema(conn, str(Path(__file__).resolve().parents[1] / "storage" / "schema.sql"))
    conn.execute(
        "INSERT INTO setup_candidates (candidate_id, as_of_ts, symbol, setup_class, direction, score, created_at, updated_at) VALUES ('c6','2026-04-01T00:00:00+00:00','SPY','x','bullish',0.5,'2026-04-01T00:00:00+00:00','2026-04-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO setup_outcomes (candidate_id, outcome_ts, pnl_abs, pnl_pct, max_favorable_excursion, max_adverse_excursion, hold_minutes, is_winner, outcome_label, notes, created_at, updated_at) VALUES ('c6','2026-04-02T00:00:00+00:00',1,1,2,1,1440,1,'ATM|synthetic_greeks|25','n','2026-04-02T00:00:00+00:00','2026-04-02T00:00:00+00:00')"
    )
    conn.commit()
    assert isinstance(query_expectancy_by_method(conn), list)
    assert isinstance(query_expectancy_by_horizon(conn), list)
    assert isinstance(query_expectancy_by_setup_and_moneyness(conn), list)
    conn.close()
