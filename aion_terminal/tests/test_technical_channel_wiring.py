"""Regression tests for the technical agreement channel.

Instrumentation over 1,142 historical arbitration rows showed the technical
channel pinned at exactly 0.500 with a single distinct value — it never
influenced a decision. Two independent defects caused that, and both are
guarded here:

1. The producer never persisted the technical read into
   ``feature_snapshots.features_json`` (only ``distances`` was written), so
   the scorer's lookups all returned None.
2. ``classify_trend`` emits ``strong_uptrend``/``strong_downtrend`` but the
   scorer matched only ``uptrend``/``downtrend``, discarding the
   highest-conviction readings.
"""

from __future__ import annotations

import json
import sqlite3

from aion_terminal.arbitration.scoring import score_technical_agreement
from aion_terminal.features.technical import (
    TechnicalFeatures,
    TechnicalState,
    classify_trend,
    compute_atr,
)
from aion_terminal.models.dto import UnderlyingBarRecord
from aion_terminal.models.enums import EMAStack, TrendState
from aion_terminal.scripts.run_daily_snapshot import _load_daily_bars, _technical_payload


def test_classify_trend_only_emits_known_vocabulary():
    """Every value the producer can emit must be a declared TrendState."""
    produced = {
        classify_trend(3, 2, 1, 1, 5),  # bullish stack, above vwap
        classify_trend(3, 2, 1, 5, 1),  # bullish stack, below vwap
        classify_trend(1, 2, 3, 5, 1),  # bearish stack, below vwap
        classify_trend(1, 2, 3, 1, 5),  # bearish stack, above vwap
        classify_trend(2, 2, 2, 1, 1),  # flat
    }
    assert produced <= {t.value for t in TrendState}


def test_strong_trend_values_are_scored():
    """`strong_uptrend` must move the score, not fall through as a no-op."""
    neutral = {"ema_stack": EMAStack.MIXED.value, "trend": TrendState.NEUTRAL.value}
    strong_up = {"ema_stack": EMAStack.MIXED.value, "trend": TrendState.STRONG_UP.value}
    strong_down = {"ema_stack": EMAStack.MIXED.value, "trend": TrendState.STRONG_DOWN.value}

    base = score_technical_agreement(neutral, "bullish")
    assert score_technical_agreement(strong_up, "bullish") > base
    assert score_technical_agreement(strong_down, "bullish") < base

    base_bear = score_technical_agreement(neutral, "bearish")
    assert score_technical_agreement(strong_down, "bearish") > base_bear
    assert score_technical_agreement(strong_up, "bearish") < base_bear


def test_plain_trend_values_still_scored():
    """The non-strong variants must keep working."""
    neutral = {"ema_stack": EMAStack.MIXED.value, "trend": TrendState.NEUTRAL.value}
    up = {"ema_stack": EMAStack.MIXED.value, "trend": TrendState.UP.value}
    base = score_technical_agreement(neutral, "bullish")
    assert score_technical_agreement(up, "bullish") > base


def _features(**over) -> TechnicalFeatures:
    f = TechnicalFeatures(
        symbol="TEST",
        timeframe="D",
        ema_stack_state=EMAStack.BULLISH.value,
        trend_state=TrendState.STRONG_UP.value,
        rvol=1.4,
        compressed=False,
        pullback_pct=2.0,
        vwap_distance_pct=1.1,
        atr=3.2,
    )
    for k, v in over.items():
        setattr(f, k, v)
    return f


def test_technical_payload_emits_keys_the_scorer_reads():
    """The persisted payload must use the scorer's lookup keys."""
    payload = _technical_payload(_features(), TechnicalState(above_vwap=True))
    for key in ("ema_stack", "trend", "rvol", "compressed", "pullback_depth"):
        assert key in payload, f"scorer reads {key!r} but payload omits it"
    assert payload["ema_stack"] == EMAStack.BULLISH.value
    assert payload["trend"] == TrendState.STRONG_UP.value


def test_pullback_depth_converted_to_fraction():
    """The scorer tests a fraction; the feature is a percentage."""
    payload = _technical_payload(_features(pullback_pct=3.0), TechnicalState())
    assert abs(payload["pullback_depth"] - 0.03) < 1e-9


def test_persisted_payload_moves_the_channel_off_its_default():
    """End-to-end: a real technical read must not score a flat 0.5.

    This is the assertion that fails against the pre-fix producer, which
    wrote only ``{"distances": ...}``.
    """
    payload = _technical_payload(_features(), TechnicalState(above_vwap=True))
    round_tripped = json.loads(json.dumps({"distances": {}, **payload}))

    aligned = score_technical_agreement(round_tripped, "bullish")
    opposed = score_technical_agreement(round_tripped, "bearish")

    assert aligned != 0.5, "technical channel is still pinned at its default"
    assert aligned > 0.5 < 1.0
    assert opposed < 0.5
    assert aligned > opposed


def test_distances_only_payload_scores_the_default():
    """The pre-fix shape is what a dead channel looks like — documented."""
    assert score_technical_agreement({"distances": {"call_wall": 1.2}}, "bullish") == 0.5


def _bars_conn():
    """A bar table carrying both corruptions seen in production data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE underlying_bars (symbol TEXT, timeframe TEXT, bar_ts TEXT, "
        "open REAL, high REAL, low REAL, close REAL, volume REAL, vwap REAL)"
    )
    rows = []
    for i in range(30):
        day = f"2026-06-{i + 1:02d}"
        price = 100.0 + i
        # Same calendar day written twice under two daily labels, with the
        # slightly different values two separate fetches produce.
        rows.append(("T", "D", f"{day}T00:00:00", price, price + 1, price - 1, price, 1_000_000, price))
        rows.append(("T", "1D", f"{day}T00:00:01", price, price + 1.1, price - 1.1, price + 0.05, 1_000_500, price))
        # Intraday bars that must never enter a daily series.
        rows.append(("T", "1H", f"{day}T15:00:00", price, price, price, price, 50_000, price))
    conn.executemany("INSERT INTO underlying_bars VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


def test_daily_bar_loader_excludes_intraday_timeframes():
    bars = _load_daily_bars(_bars_conn(), "T")
    assert bars, "loader returned no bars"
    assert all(b.timeframe.upper() in ("D", "1D", "DAILY") for b in bars)


def test_daily_bar_loader_returns_one_bar_per_calendar_day():
    """A duplicated day corrupts every downstream indicator."""
    bars = _load_daily_bars(_bars_conn(), "T")
    days = [b.bar_ts[:10] for b in bars]
    assert len(days) == len(set(days)), f"{len(days) - len(set(days))} duplicate days in series"


def test_daily_bar_loader_returns_oldest_first():
    """Indicator functions assume chronological order."""
    bars = _load_daily_bars(_bars_conn(), "T")
    assert [b.bar_ts for b in bars] == sorted(b.bar_ts for b in bars)


def test_deduplication_changes_indicator_output():
    """Guards the fix: duplicated days must not survive into the indicators.

    Zero-range repeat days deflate ATR; against production data this shifted
    ATR by 9-19% and flipped one symbol's trend classification.
    """
    conn = _bars_conn()
    clean = _load_daily_bars(conn, "T")

    raw = conn.execute(
        "SELECT symbol,timeframe,bar_ts,open,high,low,close,volume,vwap "
        "FROM underlying_bars ORDER BY bar_ts DESC LIMIT 60"
    ).fetchall()
    corrupted = [
        UnderlyingBarRecord(
            symbol=r["symbol"], timeframe=r["timeframe"], bar_ts=r["bar_ts"],
            open=r["open"], high=r["high"], low=r["low"], close=r["close"],
            volume=r["volume"], vwap=r["vwap"],
        )
        for r in reversed(raw)
    ]

    assert compute_atr(clean) != compute_atr(corrupted)


def test_macro_neutral_regime_is_symmetric():
    """A directionless macro regime must not favour one side.

    Same defect class as P0-3: `neutral`/`mixed` gave bullish +0.05 and
    bearish nothing, a free long bias on every neutral-macro day.
    """
    from aion_terminal.arbitration.scoring import score_macro_agreement

    for regime in ("neutral", "mixed"):
        bull = score_macro_agreement({"regime": regime}, "bullish")
        bear = score_macro_agreement({"regime": regime}, "bearish")
        assert bull == bear, f"{regime}: bullish={bull} bearish={bear}"


def test_macro_directional_regimes_stay_directional():
    """Symmetry on neutral must not flatten genuinely directional regimes."""
    from aion_terminal.arbitration.scoring import score_macro_agreement

    assert score_macro_agreement({"regime": "risk_on"}, "bullish") > score_macro_agreement(
        {"regime": "risk_on"}, "bearish"
    )
    assert score_macro_agreement({"regime": "risk_off"}, "bearish") > score_macro_agreement(
        {"regime": "risk_off"}, "bullish"
    )
