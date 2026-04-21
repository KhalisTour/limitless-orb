from __future__ import annotations

from aion_terminal.data_sources.marketdata import ExpiryCandidate, select_relevant_expiry_buckets


def test_select_relevant_expiry_buckets_includes_weekly_ranges_and_monthly_anchor():
    candidates = [
        ExpiryCandidate(expiry="2026-04-10", dte=1, is_monthly_anchor=False),
        ExpiryCandidate(expiry="2026-04-17", dte=8, is_monthly_anchor=True),
        ExpiryCandidate(expiry="2026-04-24", dte=15, is_monthly_anchor=False),
        ExpiryCandidate(expiry="2026-04-03", dte=0, is_monthly_anchor=False),
        ExpiryCandidate(expiry="2026-04-11", dte=2, is_monthly_anchor=False),
        ExpiryCandidate(expiry="2026-04-14", dte=5, is_monthly_anchor=False),
        ExpiryCandidate(expiry="2026-04-21", dte=12, is_monthly_anchor=False),
    ]

    selected = select_relevant_expiry_buckets(candidates)
    expiries = {c.expiry for c in selected}

    assert "2026-04-03" in expiries  # 0 DTE bucket
    assert "2026-04-11" in expiries  # 0-2 DTE bucket
    assert "2026-04-14" in expiries  # 3-7 DTE bucket
    assert "2026-04-21" in expiries  # 8-14 DTE bucket
    assert "2026-04-17" in expiries  # nearest monthly anchor
