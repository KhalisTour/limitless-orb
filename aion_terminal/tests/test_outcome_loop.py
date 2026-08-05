"""Tests for the outcome feedback loop (P1-4) and its evidence discipline.

The loop is what lets the system be evaluated at all. These tests protect the
two ways it can quietly produce a confident-looking number from nothing: an
outcome scored against a spot that was never observed, and an expectancy
modifier saturated by a handful of samples.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from aion_terminal.arbitration.adaptive import (
    EXPECTANCY_FULL_CONFIDENCE_N,
    _modifier_from_pnl,
)
from aion_terminal.utils.bars import load_daily_bars_after, load_recent_daily_bars
from aion_terminal.storage.db import bootstrap_schema, get_connection


def _conn():
    conn = get_connection(":memory:")
    bootstrap_schema(conn, str(Path(__file__).resolve().parents[1] / "storage" / "schema.sql"))
    return conn


def _seed_bars(conn):
    rows = []
    for i in range(20):
        day = f"2026-06-{i + 1:02d}"
        price = 100.0 + i
        rows.append(("T", "D", f"{day}T00:00:00", price, price + 1, price - 1, price, 1_000_000, price, day, day))
        rows.append(("T", "1D", f"{day}T00:00:01", price, price + 1, price - 1, price + 0.05, 1_000_100, price, day, day))
        rows.append(("T", "1H", f"{day}T15:00:00", price, price, price, price, 40_000, price, day, day))
    conn.executemany(
        "INSERT INTO underlying_bars (symbol, timeframe, bar_ts, open, high, low, close, volume, vwap, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


# --------------------------- shared bar loading ---------------------------


def test_forward_bars_exclude_intraday_and_duplicates():
    """A duplicated day would count as two days of holding period."""
    conn = _conn()
    _seed_bars(conn)
    bars = load_daily_bars_after(conn, "T", "2026-06-01T00:00:00", 10)
    days = [b["bar_ts"][:10] for b in bars]
    assert len(days) == len(set(days)), "duplicate calendar days in the forward window"
    assert all(b["timeframe"].upper() in ("D", "1D", "DAILY") for b in bars)


def test_forward_bars_are_chronological():
    conn = _conn()
    _seed_bars(conn)
    bars = load_daily_bars_after(conn, "T", "2026-06-01T00:00:00", 10)
    assert [b["bar_ts"] for b in bars] == sorted(b["bar_ts"] for b in bars)


def test_forward_bars_respect_the_start_timestamp():
    conn = _conn()
    _seed_bars(conn)
    bars = load_daily_bars_after(conn, "T", "2026-06-10T00:00:00", 10)
    assert bars and all(b["bar_ts"] >= "2026-06-10" for b in bars)


def test_recent_bars_are_deduplicated_and_oldest_first():
    conn = _conn()
    _seed_bars(conn)
    bars = load_recent_daily_bars(conn, "T", 10)
    days = [b["bar_ts"][:10] for b in bars]
    assert len(days) == len(set(days))
    assert [b["bar_ts"] for b in bars] == sorted(b["bar_ts"] for b in bars)


# ------------------------- expectancy shrinkage -------------------------


def test_small_samples_cannot_saturate_the_modifier():
    """Six trades must not produce a maximum-strength sizing signal."""
    saturating_pnl = 127.0  # would map to +1.0 unshrunk
    assert _modifier_from_pnl(saturating_pnl) == 1.0
    assert _modifier_from_pnl(saturating_pnl, sample_count=6) < 0.25


def test_modifier_grows_with_evidence():
    pnl = 50.0
    small = _modifier_from_pnl(pnl, sample_count=5)
    medium = _modifier_from_pnl(pnl, sample_count=30)
    large = _modifier_from_pnl(pnl, sample_count=300)
    assert small < medium < large <= 1.0


def test_shrinkage_is_symmetric_for_losses():
    """A thin sample must not argue strongly in either direction."""
    up = _modifier_from_pnl(100.0, sample_count=6)
    down = _modifier_from_pnl(-100.0, sample_count=6)
    assert abs(up + down) < 1e-9


def test_half_weight_at_the_configured_sample_count():
    assert abs(_modifier_from_pnl(50.0, sample_count=EXPECTANCY_FULL_CONFIDENCE_N) - 0.5) < 1e-9


def test_zero_samples_yields_no_signal():
    assert _modifier_from_pnl(100.0, sample_count=0) == 0.0


def test_modifier_stays_in_range():
    for n in (0, 1, 5, 50, 1000):
        for pnl in (-500.0, -50.0, 0.0, 50.0, 500.0):
            assert -1.0 <= _modifier_from_pnl(pnl, sample_count=n) <= 1.0


# ---------------------- no fabricated spot in outcomes ----------------------


def test_candidate_without_usable_spot_is_skipped_not_invented():
    """A strike is not a spot, and 100.0 is not anything.

    Previously the scorer fell back to `strike or 100.0`, selecting contracts
    against an underlying price that was never observed and feeding the
    resulting noise into expectancy — and therefore into position sizing.
    """
    from aion_terminal.backtests import outcomes

    conn = _conn()
    # A candidate with no bars at all for its symbol.
    conn.execute(
        "INSERT INTO setup_candidates (candidate_id, as_of_ts, symbol, setup_class, direction, "
        "score, strike, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("c1", "2026-06-01T00:00:00+00:00", "NOBARS", "x", "bullish", 1.0, 999.0,
         "2026-06-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"),
    )
    conn.commit()

    called = {"n": 0}

    def _should_not_be_called(*a, **k):
        called["n"] += 1
        raise AssertionError("contracts scored against a fabricated spot")

    import aion_terminal.backtests.outcomes as mod

    original = mod.score_and_rank_contracts
    mod.score_and_rank_contracts = _should_not_be_called
    try:
        results = outcomes.run_backtest_for_universe(conn, ["NOBARS"], 5, lookback_days=3650)
    finally:
        mod.score_and_rank_contracts = original

    assert results == []
    assert called["n"] == 0
