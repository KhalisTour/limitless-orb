"""Tests for the computed chart read and its persistence (P2-9)."""

from __future__ import annotations

from pathlib import Path

from aion_terminal.agents.chart_agent import (
    COMPUTED_MODEL,
    analyze_chart_computed,
    save_chart_analysis,
)
from aion_terminal.models.enums import EMAStack
from aion_terminal.storage.db import bootstrap_schema, get_connection


def _conn():
    conn = get_connection(":memory:")
    bootstrap_schema(conn, str(Path(__file__).resolve().parents[1] / "storage" / "schema.sql"))
    return conn


def _seed(conn, symbol="T", trend="up", n=60):
    rows = []
    price = 100.0
    for i in range(n):
        day = f"2026-{(i // 28) + 4:02d}-{(i % 28) + 1:02d}"
        price = price * (1.01 if trend == "up" else 0.99 if trend == "down" else 1.0)
        rows.append(
            (symbol, "1D", f"{day}T00:00:00", price, price * 1.005, price * 0.995,
             price, 1_000_000, price, day, day)
        )
    conn.executemany(
        "INSERT INTO underlying_bars (symbol, timeframe, bar_ts, open, high, low, close, "
        "volume, vwap, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn


def test_computed_read_needs_no_image_or_model():
    conn = _seed(_conn())
    result = analyze_chart_computed(conn, "T")
    assert result.model == COMPUTED_MODEL
    assert result.error is None


def test_uptrend_reads_bullish():
    conn = _seed(_conn(), trend="up")
    result = analyze_chart_computed(conn, "T")
    assert result.bias == "bullish"
    assert result.ema_stack == EMAStack.BULLISH.value


def test_downtrend_reads_bearish():
    conn = _seed(_conn(), trend="down")
    result = analyze_chart_computed(conn, "T")
    assert result.bias == "bearish"
    assert result.ema_stack == EMAStack.BEARISH.value


def test_the_read_is_deterministic():
    """The same bars must always give the same answer — an image read cannot."""
    conn = _seed(_conn())
    a = analyze_chart_computed(conn, "T")
    b = analyze_chart_computed(conn, "T")
    assert (a.bias, a.setup_score, a.ema_stack, a.trend) == (b.bias, b.setup_score, b.ema_stack, b.trend)


def test_missing_bars_are_reported_not_guessed():
    result = analyze_chart_computed(_conn(), "NOBARS")
    assert result.bias == "neutral"
    assert result.setup_class == "none"
    assert "no_bars" in result.warnings
    assert result.invalidation_price_estimate is None


def test_thin_history_is_flagged():
    conn = _seed(_conn(), n=20)
    result = analyze_chart_computed(conn, "T")
    assert any(w.startswith("only_") for w in result.warnings)


def test_relative_strength_absence_is_stated():
    """RS comes from the scanner; its absence is reported, not substituted."""
    conn = _seed(_conn())
    result = analyze_chart_computed(conn, "T")
    assert "rs_score_unavailable" in result.warnings
    assert "RS unavailable" in result.brief
    assert result.dealer_context["rs_score"] is None


def test_oscillator_fields_are_explicitly_uncomputed():
    """This system measures relative strength with rs_score, not an RSI."""
    conn = _seed(_conn())
    result = analyze_chart_computed(conn, "T")
    assert result.rsi_level is None
    assert result.rsi_divergence == "not_computed"


def test_setup_score_is_bounded():
    for trend in ("up", "down", "flat"):
        conn = _seed(_conn(), trend=trend)
        assert 0 <= analyze_chart_computed(conn, "T").setup_score <= 100


def test_invalidation_sits_on_the_correct_side():
    up = analyze_chart_computed(_seed(_conn(), trend="up"), "T")
    assert up.invalidation_price_estimate is not None
    assert up.invalidation_price_estimate < up.dealer_context["resistance"]

    down = analyze_chart_computed(_seed(_conn(), trend="down"), "T")
    assert down.invalidation_price_estimate is not None
    assert down.invalidation_price_estimate > down.dealer_context["support"]


def test_analysis_is_persisted():
    """The in-memory deque held 50 entries and died with the process."""
    conn = _seed(_conn())
    result = analyze_chart_computed(conn, "T")
    analysis_id = save_chart_analysis(conn, result)
    assert analysis_id

    row = conn.execute(
        "SELECT symbol, source, bias, setup_class FROM chart_analyses WHERE analysis_id = ?",
        (analysis_id,),
    ).fetchone()
    assert row["symbol"] == "T"
    assert row["source"] == COMPUTED_MODEL
    assert row["bias"] == result.bias


def test_persistence_failure_returns_none_rather_than_raising():
    import sqlite3

    conn = _seed(_conn())
    result = analyze_chart_computed(conn, "T")
    broken = sqlite3.connect(":memory:")  # no chart_analyses table
    assert save_chart_analysis(broken, result) is None
