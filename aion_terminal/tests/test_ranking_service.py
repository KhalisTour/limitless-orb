from __future__ import annotations

from aion_terminal.features.contracts import ContractRecommendation
from aion_terminal.services import ranking_service
from aion_terminal.services.ranking_service import (
    SymbolRanking,
    rank_symbol,
    rank_universe,
    serialize_contract,
    serialize_signal,
)
from aion_terminal.signals.setups import SetupSignal


class DummyConn:
    def execute(self, *_args, **_kwargs):
        class _Result(list):
            def fetchall(self_inner):
                return []

        return _Result()

    def close(self):
        return None


def test_rank_symbol_returns_symbol_ranking(monkeypatch):
    monkeypatch.setattr(ranking_service, "_open_connection", lambda: DummyConn())
    monkeypatch.setattr(
        ranking_service,
        "query_latest_chain",
        lambda _conn, _symbol: [
            {"symbol": "SPY", "underlying_price": 100.0, "strike": 100, "gamma": 0.1, "open_interest": 10, "type": "call"},
            {"symbol": "SPY", "underlying_price": 100.0, "strike": 99, "gamma": 0.1, "open_interest": 10, "type": "put"},
        ],
    )
    monkeypatch.setattr(
        ranking_service,
        "compute_levels",
        lambda *_args, **_kwargs: {
            "symbol": "SPY",
            "spot": 100.0,
            "regime": "trend",
            "king_node": 100.0,
            "call_wall": 105.0,
            "put_wall": 95.0,
            "distances": {},
            "curve": [],
        },
    )
    monkeypatch.setattr(
        ranking_service,
        "build_technical_features",
        lambda _bars, _tf: (ranking_service.TechnicalFeatures(symbol="SPY", rvol=1.0), ranking_service.TechnicalState()),
    )
    monkeypatch.setattr(ranking_service, "evaluate_symbol_snapshot", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        ranking_service,
        "score_and_rank_contracts",
        lambda *_args, **_kwargs: ContractRecommendation(
            symbol="SPY", bias="bearish", spot=100.0, best=None, safer=None, convex=None, all_scored=[], warnings=[]
        ),
    )

    result = rank_symbol("SPY")
    assert isinstance(result, SymbolRanking)
    assert result.symbol == "SPY"
    assert result.errors == []


def test_rank_symbol_handles_exception_gracefully(monkeypatch):
    monkeypatch.setattr(ranking_service, "_open_connection", lambda: DummyConn())
    monkeypatch.setattr(ranking_service, "query_latest_chain", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    result = rank_symbol("SPY")
    assert result.errors


def test_rank_universe_sorts_signals_first(monkeypatch):
    with_signals = SymbolRanking(
        symbol="SPY",
        spot=100.0,
        regime="trend",
        king_node=100.0,
        call_wall=105.0,
        put_wall=95.0,
        signals=[{"confidence_raw": 0.8}],
        best_contract=None,
        safer_contract=None,
        convex_contract=None,
        contract_warnings=[],
        dealer_distances={},
        bar_count=0,
        ema_stack="mixed",
        trend="neutral",
        rvol=1.0,
        compressed=False,
        ranked_at="now",
        errors=[],
    )
    without_signals = SymbolRanking(
        symbol="QQQ",
        spot=100.0,
        regime="trend",
        king_node=100.0,
        call_wall=105.0,
        put_wall=95.0,
        signals=[],
        best_contract=None,
        safer_contract=None,
        convex_contract=None,
        contract_warnings=[],
        dealer_distances={},
        bar_count=0,
        ema_stack="mixed",
        trend="neutral",
        rvol=1.0,
        compressed=False,
        ranked_at="now",
        errors=[],
    )

    def fake_rank_symbol(symbol, **_kwargs):
        return without_signals if symbol == "SPY" else with_signals

    monkeypatch.setattr(ranking_service, "rank_symbol", fake_rank_symbol)

    results = rank_universe(["SPY", "QQQ"])
    assert results[0].signals


def test_serialize_signal_produces_dict():
    signal = SetupSignal(
        setup_class="momentum_continuation",
        bias="bullish",
        preconditions_met=True,
        confidence_raw=0.8,
        score_components={"dealer_alignment": 0.3},
        reason_json='["reason"]',
        invalidation_price=100.0,
        invalidation_rule="close_below_vwap",
        warnings=[],
    )
    out = serialize_signal(signal)
    assert isinstance(out, dict)
    assert out["setup_class"] == "momentum_continuation"


def test_serialize_contract_returns_none_for_none():
    assert serialize_contract(None) is None
