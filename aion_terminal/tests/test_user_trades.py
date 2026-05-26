from __future__ import annotations

import asyncio

from aion_terminal.api import routes_trades
from aion_terminal.storage.db import bootstrap_schema, get_connection


def _setup(tmp_path, monkeypatch):
    db = str(tmp_path / "trades.db")
    monkeypatch.setattr(routes_trades.settings, "db_path", db)
    conn = get_connection(db)
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    conn.close()
    return db


def test_log_trade_minimal_fields(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    body = routes_trades.UserTradeRequest(symbol="ionq", direction="long")
    res = asyncio.run(routes_trades.log_user_trade(body))
    assert res["status"] == "logged"
    assert res["trade_id"]


def test_log_trade_computes_pnl(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    body = routes_trades.UserTradeRequest(
        symbol="PLTR", direction="long",
        entry_price=1.00, exit_price=1.50, contracts=2,
    )
    res = asyncio.run(routes_trades.log_user_trade(body))
    # (1.50 - 1.00) * 2 * 100 = 100
    assert res["pnl_dollars"] == 100.0
    assert abs(res["pnl_pct"] - 50.0) < 1e-6


def test_trade_history_filters_by_symbol(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    asyncio.run(routes_trades.log_user_trade(routes_trades.UserTradeRequest(symbol="AAA", direction="long")))
    asyncio.run(routes_trades.log_user_trade(routes_trades.UserTradeRequest(symbol="BBB", direction="long")))
    aaa = asyncio.run(routes_trades.get_trade_history(symbol="AAA", days=30))
    bbb = asyncio.run(routes_trades.get_trade_history(symbol="BBB", days=30))
    assert len(aaa) == 1 and aaa[0]["symbol"] == "AAA"
    assert len(bbb) == 1 and bbb[0]["symbol"] == "BBB"


def test_trade_summary_win_rate(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    # 2 winners, 1 loser
    for ep, xp in [(1.0, 2.0), (1.0, 1.5), (1.0, 0.5)]:
        asyncio.run(routes_trades.log_user_trade(routes_trades.UserTradeRequest(
            symbol="ZZZ", direction="long", entry_price=ep, exit_price=xp, contracts=1,
        )))
    s = asyncio.run(routes_trades.get_trade_summary(days=30))
    assert s["total_trades"] == 3
    assert s["closed_trades"] == 3
    assert abs(s["win_rate"] - (2 / 3)) < 1e-6
    assert s["best_pnl_pct"] == 100.0
    assert s["worst_pnl_pct"] == -50.0
