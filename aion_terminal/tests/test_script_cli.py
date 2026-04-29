from __future__ import annotations

from aion_terminal.scripts import bootstrap_history, run_backfill, run_backtest, run_daily_snapshot


def test_bootstrap_history_parse_args(monkeypatch):
    monkeypatch.setattr("sys.argv", ["bootstrap_history.py", "--backfill-days", "30"])
    args = bootstrap_history.parse_args()
    assert args.backfill_days == 30


def test_run_backfill_parse_args(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["run_backfill.py", "--symbols", "AAPL,SPY", "--days", "10", "--timeframes", "15m,1d", "--dry-run"],
    )
    args = run_backfill.parse_args()
    assert args.symbols == "AAPL,SPY"
    assert args.days == 10
    assert args.timeframes == "15m,1d"
    assert args.dry_run is True


def test_run_daily_snapshot_parse_args(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_daily_snapshot.py", "--symbols", "QQQ", "--max-symbols", "2", "--no-setups"])
    args = run_daily_snapshot.parse_args()
    assert args.symbols == "QQQ"
    assert args.max_symbols == 2
    assert args.no_setups is True


def test_run_daily_snapshot_prioritize(monkeypatch):
    class Rank:
        def __init__(self, symbol):
            self.symbol = symbol

    monkeypatch.setattr(run_daily_snapshot, "rank_universe", lambda symbols=None: [Rank("SPY"), Rank("AAPL")])
    out = run_daily_snapshot._prioritize_symbols(["AAPL", "SPY", "QQQ"], max_symbols=2)
    assert out == ["SPY", "AAPL"]


def test_run_backtest_parse_args(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_backtest.py", "--symbols", "META,GOOG", "--horizon-days", "5", "--method", "synthetic_greeks"])
    args = run_backtest.parse_args()
    assert args.symbols == "META,GOOG"
    assert args.horizon_days == 5
    assert args.method == "synthetic_greeks"
