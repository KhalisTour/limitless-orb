from aion_terminal.services import dashboard_service, ranking_service
from aion_terminal.services.ranking_service import SymbolRanking


def _mk(symbol, score, cqs=None, vas=None, setup_class="trend"):
    sig = {"setup_class": setup_class, "bias": "bullish", "confidence_raw": score}
    best = None
    if cqs is not None:
        best = {
            "contract_symbol": f"{symbol}C", "expiry": "2026-06-01", "strike": 100,
            "premium_mid": 1.0, "delta": 0.3, "gamma": 0.01, "theta": -0.02,
            "open_interest": 100, "spread_pct": 0.1,
            "contract_quality_score": cqs, "volatility_alignment_score": vas,
        }
    return SymbolRanking(symbol=symbol, spot=100, regime="neutral", king_node=100, call_wall=None, put_wall=None,
                         signals=[sig], best_contract=best, safer_contract=None, convex_contract=None,
                         contract_warnings=[], dealer_distances={}, bar_count=10, ema_stack="mixed", trend="up",
                         rvol=1.0, compressed=False, ranked_at="2026-05-01T00:00:00Z", errors=[])


def test_rankings_contract_and_fields(monkeypatch):
    monkeypatch.setattr(ranking_service, "rank_universe", lambda **kwargs: [_mk("AAA", 0.8, 0.2, 0.1), _mk("BBB", 0.7, None)])
    items, _ = ranking_service.get_rankings_unified(limit=5)
    by = {i.symbol: i for i in items}
    assert by["AAA"].top_contract is not None
    assert by["BBB"].top_contract == {}
    assert "missing_top_contract" in by["BBB"].warnings
    assert by["AAA"].actionability == "high"
    assert by["BBB"].actionability == "medium"
    # has_trade_plan was `bool(symbol)` — always True and therefore useless to a
    # consumer. It now reflects whether a plan actually exists, and none has
    # been generated in this fixture (P2-6).
    assert by["AAA"].has_trade_plan is False


def test_rankings_secondary_sort(monkeypatch):
    monkeypatch.setattr(ranking_service, "rank_universe", lambda **kwargs: [_mk("A", 0.8, 0.2, 0.9), _mk("B", 0.8, 0.9, 0.1)])
    items, _ = ranking_service.get_rankings_unified(limit=2)
    assert [i.symbol for i in items] == ["B", "A"]


def test_dashboard_exec_summary_and_stale(monkeypatch):
    monkeypatch.setattr(dashboard_service, "get_rankings_unified", lambda **kwargs: ([], []))
    monkeypatch.setattr(dashboard_service, "load_latest_brief", lambda: {
        "generated_at": "2026-05-01T00:00:00Z", "exec_summary": "Regime: risk-on. Risk level: medium. Trading posture: selective long.",
        "full_text": "First sentence. Second sentence. Third sentence.",
    })
    class DummyConn:
        def execute(self, q):
            class R:
                def fetchone(self_inner):
                    if "MAX(created_at)" in q:
                        return [None]
                    return [0]
            return R()
        def close(self):
            pass
    monkeypatch.setattr(dashboard_service, "get_connection", lambda _: DummyConn())
    monkeypatch.setattr(dashboard_service, "bootstrap_schema", lambda *args, **kwargs: None)

    out = dashboard_service.get_dashboard()
    assert out["macro_brief"]["summary"].startswith("Regime: risk-on")
    assert out["system_status"]["is_stale"] is True
    assert out["system_status"]["stale_minutes"] is None
