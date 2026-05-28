from __future__ import annotations

from urllib.error import HTTPError

from aion_terminal.data_sources import marketdata
from aion_terminal.data_sources.marketdata import ExpiryCandidate, MarketDataClient, select_relevant_expiry_buckets
from aion_terminal.services import ingestion_service


def test_select_relevant_expiry_buckets_includes_weekly_ranges_and_monthly_anchor():
    # New selection rules: up to 6 expiries within the next 14 days, DTE>=2.
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

    assert "2026-04-03" not in expiries  # DTE 0 skipped
    assert "2026-04-10" not in expiries  # DTE 1 skipped
    assert "2026-04-24" not in expiries  # DTE 15 over horizon
    assert "2026-04-11" in expiries
    assert "2026-04-14" in expiries
    assert "2026-04-17" in expiries
    assert "2026-04-21" in expiries
    assert len(selected) <= 6


def test_marketdata_client_retries_on_429_and_backoff(monkeypatch):
    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 429, "Too Many Requests", hdrs=None, fp=None)

    monkeypatch.setattr(marketdata, "urlopen", fake_urlopen)
    monkeypatch.setattr(marketdata.time, "sleep", fake_sleep)

    client = MarketDataClient(token="dummy")
    result = client._request_json("/test")

    assert result.ok is False
    assert result.error == "rate_limited"
    assert sleep_calls == [5, 15, 45]


def test_backfill_option_history_404_is_non_fatal(monkeypatch):
    class DummyConn:
        def close(self):
            return None

    def fake_fetch_option_history(self, contract_symbol, start_date, end_date):
        return marketdata.FetchResult(ok=False, status_code=404, payload=None, error="http_404")

    monkeypatch.setattr(ingestion_service, "_open_conn", lambda: DummyConn())
    monkeypatch.setattr(marketdata.MarketDataClient, "fetch_option_history", fake_fetch_option_history)

    result = ingestion_service.backfill_option_history("AAPL", "2026-01-01", "2026-02-01")

    assert result.ok is True
    assert result.option_history_rows == 0
