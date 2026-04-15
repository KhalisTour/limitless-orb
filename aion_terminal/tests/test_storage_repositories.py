from __future__ import annotations

from pathlib import Path

from aion_terminal.models.dto import FeatureSnapshotRecord, SetupCandidateRecord, UnderlyingBarRecord
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.storage.repositories import (
    insert_feature_snapshots,
    insert_setup_candidates,
    query_latest_feature_snapshot_by_symbol,
    query_ranked_candidates_by_date_range,
    upsert_underlying_bars,
)


def _memory_conn():
    conn = get_connection(":memory:")
    schema_path = Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
    bootstrap_schema(conn, str(schema_path))
    return conn


def test_upsert_underlying_bars_updates_existing_row():
    conn = _memory_conn()
    try:
        first = UnderlyingBarRecord(
            symbol="SPY",
            timeframe="1m",
            bar_ts="2026-04-09T14:30:00+00:00",
            open=500.0,
            high=501.0,
            low=499.5,
            close=500.4,
            volume=1000,
        )
        second = UnderlyingBarRecord(
            symbol="SPY",
            timeframe="1m",
            bar_ts="2026-04-09T14:30:00+00:00",
            open=500.0,
            high=502.0,
            low=499.5,
            close=501.2,
            volume=1200,
        )

        upsert_underlying_bars(conn, [first])
        upsert_underlying_bars(conn, [second])
        row = conn.execute("SELECT close, volume FROM underlying_bars WHERE symbol='SPY'").fetchone()
        assert row["close"] == 501.2
        assert row["volume"] == 1200
    finally:
        conn.close()


def test_query_latest_feature_snapshot_by_symbol_returns_latest():
    conn = _memory_conn()
    try:
        insert_feature_snapshots(
            conn,
            [
                FeatureSnapshotRecord(snapshot_ts="2026-04-09T14:30:00+00:00", symbol="QQQ", regime="trend"),
                FeatureSnapshotRecord(snapshot_ts="2026-04-09T14:31:00+00:00", symbol="QQQ", regime="range"),
            ],
        )
        latest = query_latest_feature_snapshot_by_symbol(conn, "QQQ")
        assert latest is not None
        assert latest.regime == "range"
    finally:
        conn.close()


def test_query_ranked_candidates_by_date_range_orders_by_score_desc():
    conn = _memory_conn()
    try:
        insert_setup_candidates(
            conn,
            [
                SetupCandidateRecord(
                    candidate_id="c1",
                    as_of_ts="2026-04-09T14:30:00+00:00",
                    symbol="AAPL",
                    setup_class="bounce",
                    score=0.65,
                ),
                SetupCandidateRecord(
                    candidate_id="c2",
                    as_of_ts="2026-04-09T14:30:00+00:00",
                    symbol="AAPL",
                    setup_class="bounce",
                    score=0.92,
                ),
            ],
        )

        rows = query_ranked_candidates_by_date_range(
            conn,
            start_ts="2026-04-09T14:00:00+00:00",
            end_ts="2026-04-09T15:00:00+00:00",
            symbol="AAPL",
            setup_class="bounce",
        )
        assert [r.candidate_id for r in rows] == ["c2", "c1"]
    finally:
        conn.close()
