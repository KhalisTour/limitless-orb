from __future__ import annotations

from aion_terminal.services import ingestion_service
from aion_terminal.services.ingestion_service import RefreshResult


def test_refresh_universe_continues_after_one_failure(monkeypatch):
    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(ingestion_service, "_open_conn", lambda: DummyConn())

    def fake_refresh(symbol, conn=None, dte_max=None):
        if symbol.upper() == "SPY":
            raise RuntimeError("unauthorized")
        return RefreshResult(symbol=symbol.upper(), ok=True, chain_rows_inserted=3)

    monkeypatch.setattr(ingestion_service, "refresh_one_symbol", fake_refresh)

    out = ingestion_service.refresh_universe(["AAPL", "SPY", "QQQ"])

    assert out["AAPL"].ok is True
    assert out["QQQ"].ok is True
    assert out["SPY"].ok is False
    assert out["SPY"].errors
