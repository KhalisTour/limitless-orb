from aion_terminal.api import routes_dashboard, routes_rankings, routes_snapshot
from aion_terminal.services import engine_service
from aion_terminal.services import ranking_service
from aion_terminal.services.ingestion_service import RefreshResult
from aion_terminal.scripts import run_daily_snapshot


def test_cache_only_contracts_does_not_call_marketdata(monkeypatch):
    monkeypatch.setattr(routes_rankings.settings, "cache_only", True)
    monkeypatch.setattr(routes_rankings, "query_latest_chain", lambda conn, symbol: [])
    out = routes_rankings.get_contracts_for_symbol("SPY")
    assert "cache_miss" in out["warnings"]


def test_cache_only_expiries_does_not_call_marketdata(monkeypatch):
    monkeypatch.setattr(routes_snapshot.settings, "cache_only", True)
    monkeypatch.setattr(routes_snapshot, "query_expiries", lambda symbol, runtime_state=None: [])
    out = routes_snapshot.get_expiries("SPY")
    assert out["cache_only"] is True
    assert "refresh_required" in out["warnings"]


def test_cache_only_levels_no_ingest(monkeypatch):
    monkeypatch.setattr(engine_service.settings, "cache_only", True)
    monkeypatch.setattr(engine_service.repositories, "query_latest_chain", lambda conn, ticker: [])
    called = {"ingest": False}
    monkeypatch.setattr(engine_service, "ingest_symbol", lambda *args, **kwargs: called.__setitem__("ingest", True))
    out = engine_service.compute_or_load_levels("SPY", type("S", (), {"latest_levels": {}})())
    assert out["cache_only"] is True
    assert called["ingest"] is False


def test_missing_cache_returns_warning_not_exception(monkeypatch):
    monkeypatch.setattr(routes_rankings.settings, "cache_only", True)
    monkeypatch.setattr(routes_rankings, "query_latest_chain", lambda conn, symbol: [])
    out = routes_rankings.get_setups_for_symbol("SPY")
    assert out["cache_only"] is True


def test_scripts_still_call_ingestion(monkeypatch):
    called = {"refresh": False}
    monkeypatch.setattr(run_daily_snapshot, "_open_conn", lambda: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(run_daily_snapshot, "query_latest_raw_chain_snapshot_ts", lambda conn, symbol: None)
    def fake_refresh(symbol, conn=None, **kwargs):
        called["refresh"] = True
        return RefreshResult(symbol=symbol, ok=True)
    monkeypatch.setattr(run_daily_snapshot, "refresh_one_symbol", fake_refresh)
    monkeypatch.setattr(run_daily_snapshot, "refresh_one_symbol_from_db", lambda symbol, conn=None: RefreshResult(symbol=symbol, ok=True))
    monkeypatch.setattr(run_daily_snapshot, "query_underlying_bars_count", lambda conn, symbol: 0)
    monkeypatch.setattr(run_daily_snapshot, "rank_universe", lambda symbols=None: [])
    monkeypatch.setattr("sys.argv", ["run_daily_snapshot.py", "--symbols", "SPY", "--max-symbols", "1", "--no-contracts", "--no-setups"])
    run_daily_snapshot.main()
    assert called["refresh"] is True


def test_cache_only_endpoints_do_not_refresh(monkeypatch):
    monkeypatch.setattr(ranking_service.settings, "cache_only", True)
    monkeypatch.setattr(routes_rankings.settings, "cache_only", True)

    def _raise(*args, **kwargs):
        raise Exception("should_not_refresh")

    for module in (ranking_service, routes_rankings, engine_service):
        monkeypatch.setattr(module, "ingest_symbol", _raise, raising=False)
        monkeypatch.setattr(module, "refresh_one_symbol", _raise, raising=False)
        monkeypatch.setattr(module, "refresh_symbol", _raise, raising=False)

    dashboard = routes_dashboard.get_dashboard_endpoint()
    rankings = routes_rankings.get_rankings()
    symbol = routes_rankings.get_ranking_symbol("SPY")

    assert isinstance(dashboard, dict)
    assert isinstance(rankings, dict)
    assert isinstance(symbol, dict)
