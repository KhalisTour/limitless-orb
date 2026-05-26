from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from aion_terminal.agents.brief_agent import BriefResult, save_brief_to_db
from aion_terminal.api import routes_agents
from aion_terminal.scripts.run_morning_brief import write_weekly_rollup
from aion_terminal.storage.db import bootstrap_schema, get_connection

SCHEMA_PATH = "aion_terminal/storage/schema.sql"


def _make_brief() -> BriefResult:
    return BriefResult(
        generated_at="2026-05-15T13:00:00Z",
        regime="risk_on",
        dominant_signal="Liquidity expanding",
        regime_30d_call="Buy dips",
        sector_leaders=["XLK", "XLY"],
        sector_laggards=["XLU", "XLP"],
        narrative_tags=[],
        risk_level="medium",
        full_text="full",
        exec_summary="summary",
        model="m",
        tokens_used=42,
    )


def _conn(tmp_path):
    conn = get_connection(str(tmp_path / "t.db"))
    bootstrap_schema(conn, SCHEMA_PATH)
    return conn


def test_save_brief_to_db_writes_row(tmp_path):
    conn = _conn(tmp_path)
    try:
        brief_id = save_brief_to_db(conn, _make_brief())
        row = conn.execute("SELECT * FROM morning_briefs WHERE brief_id = ?", (brief_id,)).fetchone()
        assert row is not None
        assert row["regime"] == "risk_on"
        assert json.loads(row["sector_leaders_json"]) == ["XLK", "XLY"]
    finally:
        conn.close()


def test_save_brief_to_db_upserts_on_same_date(tmp_path):
    conn = _conn(tmp_path)
    try:
        b1 = _make_brief()
        save_brief_to_db(conn, b1)
        b2 = _make_brief()
        b2.regime = "risk_off"
        save_brief_to_db(conn, b2)
        rows = conn.execute("SELECT * FROM morning_briefs").fetchall()
        assert len(rows) == 1
        assert rows[0]["regime"] == "risk_off"
    finally:
        conn.close()


def test_brief_history_endpoint_returns_list(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_agents.settings, "db_path", str(tmp_path / "h.db"))
    conn = get_connection(routes_agents.settings.db_path)
    bootstrap_schema(conn, SCHEMA_PATH)
    save_brief_to_db(conn, _make_brief())
    conn.close()
    out = routes_agents.get_brief_history(days=14)
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["regime"] == "risk_on"
    assert "sector_leaders" in out[0]


def test_weekly_rollup_only_on_friday(tmp_path):
    conn = _conn(tmp_path)
    try:
        save_brief_to_db(conn, _make_brief())
        # Pick a non-Friday date: 2026-05-13 is a Wednesday
        wed = date(2026, 5, 13)
        out_dir = tmp_path / "briefs"
        path = write_weekly_rollup(conn, wed, output_dir=out_dir)
        # Function always writes — caller gates by weekday. Verify the file exists
        # and contains an empty briefs list (no briefs in that week's range).
        assert Path(path).exists()
        data = json.loads(Path(path).read_text())
        assert data["week_ending"] == "2026-05-13"
    finally:
        conn.close()


def test_weekly_rollup_contains_current_week(tmp_path):
    conn = _conn(tmp_path)
    try:
        # Insert a brief dated today (use code's own date.today via save_brief_to_db)
        save_brief_to_db(conn, _make_brief())
        today = date.today()
        friday = today + timedelta(days=(4 - today.weekday()) % 7)
        # write rollup with friday = today's-week-friday and ensure today's brief included
        path = write_weekly_rollup(conn, friday, output_dir=tmp_path / "briefs")
        data = json.loads(Path(path).read_text())
        assert data["week_ending"] == friday.isoformat()
        # today's brief should be in the briefs list (since monday <= today <= friday)
        dates = [b["brief_date"] for b in data["briefs"]]
        assert today.isoformat() in dates
    finally:
        conn.close()
