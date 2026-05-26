from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from aion_terminal.api import routes_health
from aion_terminal.storage.db import bootstrap_schema, get_connection


def test_cache_invalidate_endpoint_returns_200():
    result = asyncio.run(routes_health.invalidate_ranking_cache())
    assert result["status"] == "invalidated"
    assert "timestamp" in result


def _seed(db_path: str, symbol: str, chain_age_h: float, bars_age_h: float, feat_age_h: float):
    conn = get_connection(db_path)
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    now = datetime.now(timezone.utc)
    def iso(h):
        return (now - timedelta(hours=h)).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO raw_chain_snapshots (snapshot_ts, symbol, option_symbol, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (iso(chain_age_h), symbol, f"{symbol}_opt", iso(0), iso(0)),
    )
    conn.execute(
        "INSERT INTO underlying_bars (symbol, timeframe, bar_ts, open, high, low, close, created_at, updated_at) "
        "VALUES (?, '1D', ?, 1, 1, 1, 1, ?, ?)",
        (symbol, iso(bars_age_h), iso(0), iso(0)),
    )
    conn.execute(
        "INSERT INTO feature_snapshots (snapshot_ts, symbol, expiry, created_at, updated_at) VALUES (?, ?, 'combined', ?, ?)",
        (iso(feat_age_h), symbol, iso(0), iso(0)),
    )
    conn.commit()
    conn.close()


def test_data_freshness_returns_all_watchlist_symbols(tmp_path, monkeypatch):
    db = str(tmp_path / "f.db")
    monkeypatch.setattr(routes_health.settings, "db_path", db)
    _seed(db, "IONQ", 0.5, 1.0, 0.5)
    _seed(db, "PLTR", 0.5, 1.0, 0.5)
    res = asyncio.run(routes_health.get_data_freshness())
    assert "symbols" in res
    assert "IONQ" in res["symbols"]
    assert "PLTR" in res["symbols"]
    assert res["symbols"]["IONQ"]["overall_status"] == "fresh"


def test_data_freshness_status_thresholds(tmp_path, monkeypatch):
    db = str(tmp_path / "f2.db")
    monkeypatch.setattr(routes_health.settings, "db_path", db)
    _seed(db, "FRESH", 0.1, 0.1, 0.1)   # all fresh
    _seed(db, "STALE", 0.1, 12.0, 0.1)  # bars stale 6-24h
    _seed(db, "MISS", 0.1, 48.0, 0.1)   # bars missing >24h
    res = asyncio.run(routes_health.get_data_freshness())
    assert res["symbols"]["FRESH"]["overall_status"] == "fresh"
    assert res["symbols"]["STALE"]["overall_status"] == "stale"
    assert res["symbols"]["MISS"]["overall_status"] == "missing"


def test_bar_timeframe_fix_reads_1d_rows(tmp_path, monkeypatch):
    from aion_terminal.services import ranking_service
    db = str(tmp_path / "bars.db")
    conn = get_connection(db)
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
    now = datetime.now(timezone.utc).isoformat()
    # Insert a row with timeframe '1D' (Yahoo backfill format)
    conn.execute(
        "INSERT INTO underlying_bars (symbol, timeframe, bar_ts, open, high, low, close, created_at, updated_at) "
        "VALUES ('TEST', '1D', ?, 10, 11, 9, 10.5, ?, ?)",
        (now, now, now),
    )
    # And an option-level bar that should be excluded
    conn.execute(
        "INSERT INTO underlying_bars (symbol, timeframe, bar_ts, open, high, low, close, created_at, updated_at) "
        "VALUES ('TEST', 'option', ?, 1, 1, 1, 1, ?, ?)",
        (now, now, now),
    )
    conn.commit()
    bars = ranking_service._load_underlying_bars(conn, "TEST")
    conn.close()
    assert len(bars) == 1
    assert bars[0].timeframe == "1D"
