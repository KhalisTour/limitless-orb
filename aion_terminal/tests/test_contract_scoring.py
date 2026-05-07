from __future__ import annotations

from pathlib import Path

from aion_terminal.features.contracts import (
    classify_moneyness,
    compute_expected_move_fit,
    compute_liquidity_score,
    score_and_rank_contracts,
    score_contract,
)
from aion_terminal.storage.db import bootstrap_schema, get_connection


def make_contract(
    strike=100.0,
    spot=100.0,
    side="call",
    dte=10,
    bid=1.80,
    ask=2.20,
    delta=0.45,
    gamma=0.08,
    theta=-0.05,
    iv=0.30,
    open_interest=500,
    volume=200,
    option_symbol="O:SPY260417C00100000",
):
    return {
        "symbol": "SPY",
        "option_symbol": option_symbol,
        "expiry": "2026-04-17",
        "side": side,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mark": (bid + ask) / 2.0,
        "iv": iv,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "open_interest": open_interest,
        "volume": volume,
        "dte": dte,
        "underlying_price": spot,
        "snapshot_ts": "2026-04-21T12:00:00+00:00",
    }


def make_oi_list(center=100.0, count=10):
    out = []
    for i in range(count):
        strike = center - (count // 2) + i
        oi = 100 + (i * 20)
        out.append((strike, oi))
    return out


def test_classify_moneyness_call_itm():
    assert classify_moneyness(95, 100, "call") == "ITM"


def test_classify_moneyness_call_atm():
    assert classify_moneyness(100, 100, "call") == "ATM"


def test_classify_moneyness_call_otm():
    assert classify_moneyness(106, 100, "call") == "OTM"


def test_classify_moneyness_put_itm():
    assert classify_moneyness(106, 100, "put") == "ITM"


def test_liquidity_score_tight_spread_high_oi():
    score = compute_liquidity_score(1.90, 2.10, 2.0, 1000, 500)
    assert score > 0.7


def test_liquidity_score_wide_spread():
    score = compute_liquidity_score(1.00, 3.00, 2.0, 50, 10)
    assert score < 0.3


def test_score_contract_returns_none_on_low_oi():
    contract = make_contract(open_interest=5)
    assert score_contract(contract, 100.0, "call", make_oi_list()) is None


def test_score_contract_returns_none_on_zero_mid():
    contract = make_contract(bid=0.0, ask=0.0)
    assert score_contract(contract, 100.0, "call", make_oi_list()) is None


def test_score_contract_valid_call():
    contract = make_contract()
    scored = score_contract(contract, 100.0, "call", make_oi_list())
    assert scored is not None
    assert scored.total_score > 0.0


def test_score_and_rank_returns_recommendation(tmp_path):
    db_path = tmp_path / "contracts.db"
    conn = get_connection(str(db_path))
    schema_path = Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
    bootstrap_schema(conn, str(schema_path))

    rows = []
    for i in range(10):
        c = make_contract(
            strike=95.0 + i,
            option_symbol=f"O:SPY260417C00{i:05d}",
            open_interest=200 + i * 20,
            delta=0.25 + i * 0.03,
        )
        rows.append(c)

    conn.executemany(
        """
        INSERT INTO raw_chain_snapshots (
            snapshot_ts, symbol, expiry, option_symbol, side, strike, bid, ask, last, mark,
            iv, delta, gamma, theta, vega, rho, open_interest, volume, dte, underlying_price,
            source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["snapshot_ts"],
                r["symbol"],
                r["expiry"],
                r["option_symbol"],
                r["side"],
                r["strike"],
                r["bid"],
                r["ask"],
                r["mark"],
                r["mark"],
                r["iv"],
                r["delta"],
                r["gamma"],
                r["theta"],
                0.0,
                0.0,
                r["open_interest"],
                r["volume"],
                r["dte"],
                r["underlying_price"],
                "test",
                r["snapshot_ts"],
                r["snapshot_ts"],
            )
            for r in rows
        ],
    )
    conn.commit()

    rec = score_and_rank_contracts(conn, "SPY", "bullish", 100.0)
    assert rec.best is not None
    conn.close()


def test_expected_move_fit_atm_call():
    fit = compute_expected_move_fit(strike=105.0, spot=100.0, iv=0.30, dte=10, side="call")
    assert fit > 0.8

def test_role_separation_prefers_balanced_best(tmp_path):
    db_path = tmp_path / "roles.db"
    conn = get_connection(str(db_path))
    schema_path = Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
    bootstrap_schema(conn, str(schema_path))
    rows = [
        make_contract(strike=100, delta=0.50, gamma=0.05, bid=4.8, ask=5.2, option_symbol="ATM"),
        make_contract(strike=110, delta=0.20, gamma=0.15, bid=0.9, ask=1.1, option_symbol="OTM"),
        make_contract(strike=97, delta=0.62, gamma=0.04, bid=6.8, ask=7.2, option_symbol="ITM"),
    ]
    conn.executemany(
        """
        INSERT INTO raw_chain_snapshots (
            snapshot_ts, symbol, expiry, option_symbol, side, strike, bid, ask, last, mark,
            iv, delta, gamma, theta, vega, rho, open_interest, volume, dte, underlying_price,
            source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(
            r["snapshot_ts"], r["symbol"], r["expiry"], r["option_symbol"], r["side"], r["strike"],
            r["bid"], r["ask"], r["mark"], r["mark"], r["iv"], r["delta"], r["gamma"], r["theta"],
            0.0, 0.0, r["open_interest"], r["volume"], r["dte"], r["underlying_price"], "test", r["snapshot_ts"], r["snapshot_ts"]
        ) for r in rows],
    )
    conn.commit()
    rec = score_and_rank_contracts(conn, "SPY", "bullish", 100.0)
    assert rec.convex is not None and rec.convex.contract_symbol == "OTM"
    assert rec.best is not None and rec.best.contract_symbol != "OTM"
    assert rec.safer is not None and abs(rec.safer.delta) >= abs(rec.best.delta)
    assert len({rec.best.contract_symbol, rec.safer.contract_symbol, rec.convex.contract_symbol}) == 3
    conn.close()
