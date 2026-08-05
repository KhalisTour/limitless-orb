from __future__ import annotations

import json

from pathlib import Path

from aion_terminal.features.technical import TechnicalState
from aion_terminal.models.dto import FeatureSnapshotRecord
from aion_terminal.services.ingestion_service import RefreshResult
from aion_terminal.scripts import bootstrap_history, run_backfill, run_backtest, run_daily_snapshot
from aion_terminal.storage.db import bootstrap_schema, get_connection


def _stub_technical_state():
    """Neutral TechnicalState standing in for build_technical_features in CLI tests."""
    return TechnicalState(
        above_vwap=True,
        ema_stack="mixed",
        trend="neutral",
        compressed=False,
        high_rvol=False,
        near_support=False,
        near_resistance=False,
        recovering_from_pullback=False,
        pullback_depth_pct=0.0,
    )


def _memory_conn():
    conn = get_connection(":memory:")
    schema_path = Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
    bootstrap_schema(conn, str(schema_path))
    return conn


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
    assert args.sleep_seconds == 2.0
    assert args.skip_refresh_if_recent_minutes == 30
    assert args.chain_only is False
    assert args.bars_only is False


def test_run_daily_snapshot_parse_args_rate_limit_flags(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_daily_snapshot.py",
            "--symbols",
            "QQQ",
            "--sleep-seconds",
            "1.5",
            "--skip-refresh-if-recent-minutes",
            "10",
            "--chain-only",
        ],
    )
    args = run_daily_snapshot.parse_args()
    assert args.sleep_seconds == 1.5
    assert args.skip_refresh_if_recent_minutes == 10
    assert args.chain_only is True
    assert args.bars_only is False


def test_run_daily_snapshot_skips_recent_refresh(monkeypatch, capsys):
    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(run_daily_snapshot, "_open_conn", lambda: DummyConn())
    monkeypatch.setattr(run_daily_snapshot, "query_latest_raw_chain_snapshot_ts", lambda conn, symbol: run_daily_snapshot.utc_now_iso())
    monkeypatch.setattr(
        run_daily_snapshot,
        "refresh_one_symbol_from_db",
        lambda symbol, conn=None: RefreshResult(symbol=symbol, ok=True),
    )
    monkeypatch.setattr(
        run_daily_snapshot,
        "refresh_one_symbol",
        lambda symbol, conn=None, **kwargs: (_ for _ in ()).throw(RuntimeError("unexpected_api_refresh")),
    )
    monkeypatch.setattr(run_daily_snapshot, "query_underlying_bars_count", lambda conn, symbol: 0)
    monkeypatch.setattr(run_daily_snapshot, "rank_universe", lambda symbols=None: [])
    monkeypatch.setattr("sys.argv", ["run_daily_snapshot.py", "--symbols", "ERAS", "--max-symbols", "1", "--no-contracts", "--no-setups"])

    result = run_daily_snapshot.main()
    captured = capsys.readouterr()

    assert result == 0
    assert "skipped_recent=True" in captured.out
    assert "refreshed=False" in captured.out


def test_run_daily_snapshot_prioritize(monkeypatch):
    class Rank:
        def __init__(self, symbol):
            self.symbol = symbol

    monkeypatch.setattr(run_daily_snapshot, "rank_universe", lambda symbols=None: [Rank("SPY"), Rank("AAPL")])
    out = run_daily_snapshot._prioritize_symbols(["AAPL", "SPY", "QQQ"], max_symbols=2)
    assert out == ["SPY", "AAPL"]


def test_build_feature_snapshots_for_refresh_creates_one_snapshot_per_expiry():
    refresh = RefreshResult(
        symbol="IONQ",
        ok=True,
        grouped_chain={
            "2026-05-01": {
                "expiry": "2026-05-01",
                "dte_min": 5,
                "dte_max": 5,
                "contracts": [],
                "contract_count": 0,
            }
        },
    )
    refresh.levels = {
        "combined_levels": {
            "spot": 100.0,
            "regime": "trend",
            "king_node": 105.0,
            "call_wall": 110.0,
            "put_wall": 90.0,
            "flip_zone": 100.0,
            "distances": {},
        },
        "expiry_levels": {
            "2026-05-01": {
                "expiry": "2026-05-01",
                "spot": 100.0,
                "regime": "trend",
                "king_node": 105.0,
                "call_wall": 110.0,
                "put_wall": 90.0,
                "flip_zone": 100.0,
                "distances": {},
            }
        },
    }

    snapshots = run_daily_snapshot._build_feature_snapshots_for_refresh("IONQ", refresh)

    # One combined-across-expiries row plus one per-expiry row (P0-1).
    assert len(snapshots) == 2
    by_expiry = {s.expiry: s for s in snapshots}
    assert set(by_expiry) == {"combined", "2026-05-01"}
    # Combined and per-expiry share a single snapshot_ts (the join key the arbiter uses).
    assert len({s.snapshot_ts for s in snapshots}) == 1

    combined = by_expiry["combined"]
    assert combined.dte is None
    assert combined.call_wall == 110.0

    per_expiry = by_expiry["2026-05-01"]
    assert per_expiry.symbol == "IONQ"
    assert per_expiry.dte == 5
    # Parsed rather than string-compared: features_json carries the dealer
    # structure read and the technical payload alongside distances.
    assert json.loads(per_expiry.features_json)["distances"] == {}


def test_run_daily_snapshot_skips_feature_snapshot_without_expiries(monkeypatch):
    inserted = False

    def fake_insert(conn, features):
        nonlocal inserted
        inserted = True
        return 0

    monkeypatch.setattr(run_daily_snapshot, "insert_feature_snapshots", fake_insert)
    monkeypatch.setattr(run_daily_snapshot, "rank_universe", lambda symbols=None: [])
    monkeypatch.setattr(run_daily_snapshot, "build_technical_features", lambda symbols, timeframe: ([], _stub_technical_state()))
    monkeypatch.setattr(run_daily_snapshot, "evaluate_symbol_snapshot", lambda **kwargs: [])
    monkeypatch.setattr(run_daily_snapshot, "score_and_rank_contracts", lambda conn, symbol, bias: type("R", (), {"best": None})())
    monkeypatch.setattr(run_daily_snapshot, "_open_conn", _memory_conn)

    refresh = RefreshResult(
        symbol="ERAS",
        ok=True,
        grouped_chain={},
        levels={
            "combined_levels": {
                "spot": 100.0,
                "regime": "range",
                "king_node": 100.0,
                "call_wall": None,
                "put_wall": None,
                "flip_zone": None,
                "distances": {},
            }
        },
    )
    monkeypatch.setattr(run_daily_snapshot, "refresh_one_symbol", lambda symbol, conn=None, **kwargs: refresh)
    monkeypatch.setattr("sys.argv", ["run_daily_snapshot.py", "--symbols", "ERAS", "--max-symbols", "1", "--no-contracts"])

    result = run_daily_snapshot.main()

    assert result == 0
    assert inserted is False


def test_run_daily_snapshot_continues_after_one_symbol_missing_expiries(monkeypatch, capsys):
    inserted = []

    def fake_insert(conn, features):
        inserted.append([feature.expiry for feature in features])
        return len(features)

    def fake_refresh(symbol, conn=None, **kwargs):
        if symbol == "IONQ":
            refresh = RefreshResult(
                symbol="IONQ",
                ok=True,
                grouped_chain={
                    "2026-05-01": {
                        "expiry": "2026-05-01",
                        "dte_min": 5,
                        "dte_max": 5,
                        "contracts": [],
                        "contract_count": 0,
                    }
                },
            )
            refresh.levels = {
                "combined_levels": {
                    "spot": 100.0,
                    "regime": "trend",
                    "king_node": 105.0,
                    "call_wall": 110.0,
                    "put_wall": 90.0,
                    "flip_zone": 100.0,
                    "distances": {},
                },
                "expiry_levels": {
                    "2026-05-01": {
                        "expiry": "2026-05-01",
                        "spot": 100.0,
                        "regime": "trend",
                        "king_node": 105.0,
                        "call_wall": 110.0,
                        "put_wall": 90.0,
                        "flip_zone": 100.0,
                        "distances": {},
                    }
                },
            }
            return refresh

        return RefreshResult(
            symbol="ERAS",
            ok=True,
            grouped_chain={},
            levels={
                "combined_levels": {
                    "spot": 100.0,
                    "regime": "range",
                    "king_node": 100.0,
                    "call_wall": None,
                    "put_wall": None,
                    "flip_zone": None,
                    "distances": {},
                }
            },
        )

    monkeypatch.setattr(run_daily_snapshot, "insert_feature_snapshots", fake_insert)
    monkeypatch.setattr(run_daily_snapshot, "rank_universe", lambda symbols=None: [])
    monkeypatch.setattr(run_daily_snapshot, "build_technical_features", lambda symbols, timeframe: ([], _stub_technical_state()))
    monkeypatch.setattr(run_daily_snapshot, "evaluate_symbol_snapshot", lambda **kwargs: [])
    monkeypatch.setattr(run_daily_snapshot, "score_and_rank_contracts", lambda conn, symbol, bias: type("R", (), {"best": None})())
    monkeypatch.setattr(run_daily_snapshot, "_open_conn", _memory_conn)
    monkeypatch.setattr(run_daily_snapshot, "refresh_one_symbol", fake_refresh)
    monkeypatch.setattr("sys.argv", ["run_daily_snapshot.py", "--symbols", "IONQ,ERAS", "--max-symbols", "2", "--no-contracts"])

    result = run_daily_snapshot.main()
    captured = capsys.readouterr()

    assert result == 0
    assert "IONQ | skipped_recent=False | refreshed=True" in captured.out
    assert "ERAS | skipped_recent=False | refreshed=True" in captured.out
    # IONQ persists a combined row plus its single per-expiry row; ERAS has an
    # empty grouped_chain and is skipped entirely.
    assert inserted == [["combined", "2026-05-01"]]


def test_run_backtest_parse_args(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_backtest.py", "--symbols", "META,GOOG", "--horizon-days", "5", "--method", "synthetic_greeks"])
    args = run_backtest.parse_args()
    assert args.symbols == "META,GOOG"
    assert args.horizon_days == 5
    assert args.method == "synthetic_greeks"
