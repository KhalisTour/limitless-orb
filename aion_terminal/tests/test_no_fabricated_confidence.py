"""Guards against the system reporting numbers it cannot justify.

The failure mode these cover is not a crash — it is a plausible-looking value
produced from no evidence. That is the specific hazard when sizing and setup
selection stop being reviewed by a human on every trade.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from aion_terminal.arbitration import arbiter
from aion_terminal.arbitration.adaptive import compute_sizing_modifier, has_expectancy_history
from aion_terminal.storage.db import bootstrap_schema, get_connection


def _conn():
    conn = get_connection(":memory:")
    bootstrap_schema(conn, str(Path(__file__).resolve().parents[1] / "storage" / "schema.sql"))
    return conn


# ----------------------------- P1-5: sizing -----------------------------


def test_sizing_is_none_without_expectancy_history():
    """No history means no size — not a small one."""
    assert compute_sizing_modifier(0.8, 0.8, 0.0, "trend", True, has_expectancy_data=False) is None


def test_sizing_is_produced_when_history_exists():
    v = compute_sizing_modifier(0.8, 0.8, 0.0, "trend", True, has_expectancy_data=True)
    assert v is not None and 0.0 < v <= 1.0


def test_old_floor_cannot_manufacture_a_size_from_nothing():
    """The 0.1 floor previously produced a position for a zero-confidence setup."""
    assert compute_sizing_modifier(0.0, 0.0, 0.0, "range", False, has_expectancy_data=False) is None


def test_has_expectancy_history_false_on_empty_table():
    assert has_expectancy_history(_conn()) is False


def test_has_expectancy_history_true_once_populated():
    conn = _conn()
    conn.execute(
        "INSERT INTO adaptive_expectancy (scope, sample_count, expectancy_modifier, updated_at) "
        "VALUES ('setup:x', 10, 0.2, '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    assert has_expectancy_history(conn) is True


def test_has_expectancy_history_false_when_table_is_absent():
    """Must report absence, not raise, against a partially migrated DB."""
    assert has_expectancy_history(sqlite3.connect(":memory:")) is False


def test_arbitration_marks_itself_unsized(monkeypatch):
    conn = _conn()
    result = arbiter.run_arbitration(
        symbol="TEST", ranking={"spot": 100.0, "call_wall": 110.0, "put_wall": 90.0, "regime": "trend"},
        setup_candidates=[{"setup_class": "momentum_continuation", "direction": "bullish"}],
        features={"ema_stack": "bullish_stack", "trend": "uptrend", "rvol": 1.2},
        contracts={"best": {"total_score": 60, "spread_pct": 0.02, "volume": 100, "dte": 7}},
        macro_brief=None, memory_summary=None, expectancy_data=None,
        rs_candidate=None, narrative_tags=None, conn=conn,
    )
    assert result.sizing_modifier is None
    assert "unsized_no_expectancy_data" in result.warnings


# -------------------------- P1-1: degraded setups --------------------------


def test_absent_setup_is_not_labelled_as_the_degraded_fallback():
    """No candidate at all is a different condition from the fallback firing."""
    assert arbiter._derive_setup_class(None) == arbiter.NO_SETUP_CLASS
    assert arbiter._derive_setup_class([]) == arbiter.NO_SETUP_CLASS
    assert arbiter._derive_setup_class(None) != arbiter.DEGRADED_SETUP_CLASS


def test_real_setup_class_passes_through():
    assert arbiter._derive_setup_class([{"setup_class": "pullback_into_support"}]) == "pullback_into_support"


def test_degraded_setup_class_is_flagged_to_consumers():
    conn = _conn()
    result = arbiter.run_arbitration(
        symbol="TEST", ranking={"spot": 100.0, "call_wall": 110.0, "regime": "trend"},
        setup_candidates=[{"setup_class": arbiter.DEGRADED_SETUP_CLASS, "direction": "bullish"}],
        features={}, contracts=None, macro_brief=None, memory_summary=None,
        expectancy_data=None, rs_candidate=None, narrative_tags=None, conn=conn,
    )
    assert "degraded_setup_class" in result.warnings


# ------------------------ P2-4: persistence failures ------------------------


def test_persistence_failure_is_counted_not_swallowed():
    """A lost reasoning record must leave a trace."""
    arbiter.reset_persistence_failure_count()
    conn = sqlite3.connect(":memory:")  # no schema — the insert cannot succeed
    conn.row_factory = sqlite3.Row

    arbiter.run_arbitration(
        symbol="TEST", ranking=None, setup_candidates=None, features=None, contracts=None,
        macro_brief=None, memory_summary=None, expectancy_data=None, rs_candidate=None,
        narrative_tags=None, conn=conn,
    )
    assert arbiter.persistence_failure_count() == 1


def test_successful_persistence_does_not_increment_the_counter():
    arbiter.reset_persistence_failure_count()
    conn = _conn()
    arbiter.run_arbitration(
        symbol="TEST", ranking=None, setup_candidates=None, features=None, contracts=None,
        macro_brief=None, memory_summary=None, expectancy_data=None, rs_candidate=None,
        narrative_tags=None, conn=conn,
    )
    assert arbiter.persistence_failure_count() == 0
