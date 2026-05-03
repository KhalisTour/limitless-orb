from __future__ import annotations

from aion_terminal.app import main as app_main
from aion_terminal.services import engine_service


def test_startup_event_cache_only_does_not_start_pipeline_thread(monkeypatch):
    monkeypatch.setattr(app_main.settings, "cache_only", True)
    monkeypatch.setattr(app_main.settings, "marketdata_enabled", True)

    called = {"thread_created": False, "thread_started": False}

    class FakeThread:
        def __init__(self, *args, **kwargs):
            called["thread_created"] = True

        def start(self):
            called["thread_started"] = True

    monkeypatch.setattr(app_main.threading, "Thread", FakeThread)

    app_main.startup_event()

    assert called["thread_created"] is False
    assert called["thread_started"] is False


def test_startup_event_starts_pipeline_when_refresh_mode_enabled(monkeypatch):
    monkeypatch.setattr(app_main.settings, "cache_only", False)
    monkeypatch.setattr(app_main.settings, "marketdata_enabled", True)

    called = {"thread_created": False, "thread_started": False}

    class FakeThread:
        def __init__(self, *args, **kwargs):
            called["thread_created"] = True

        def start(self):
            called["thread_started"] = True

    monkeypatch.setattr(app_main.threading, "Thread", FakeThread)

    app_main.startup_event()

    assert called["thread_created"] is True
    assert called["thread_started"] is True


def test_pipeline_loop_cache_only_returns_without_ingesting(monkeypatch):
    monkeypatch.setattr(engine_service.settings, "cache_only", True)
    monkeypatch.setattr(engine_service.settings, "marketdata_enabled", True)

    called = {"ingest": False}

    def fake_ingest(*args, **kwargs):
        called["ingest"] = True
        raise AssertionError("ingest_symbol should not be called in cache-only mode")

    monkeypatch.setattr(engine_service, "ingest_symbol", fake_ingest)

    engine_service.pipeline_loop(
        type("RuntimeState", (), {"latest_levels": {}, "active_connections": []})()
    )

    assert called["ingest"] is False
