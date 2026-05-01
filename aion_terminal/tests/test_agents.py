from __future__ import annotations

import json
import sys
import types


# Provide dotenv shim for environments without python-dotenv installed.
if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None)

from aion_terminal.agents.brief_agent import BriefResult, generate_morning_brief, post_brief_tags
from aion_terminal.agents.chart_agent import analyze_chart, analyze_chart_from_bytes
from aion_terminal.agents.prompts import CHART_ANALYSIS_SYSTEM_PROMPT, MACRO_BRIEF_SYSTEM_PROMPT
from aion_terminal.storage.db import bootstrap_schema, get_connection


def fake_brief_response():
    class FakeResponse:
        output_text = "The macro regime is stagflationary risk-off.\n```json\n" + json.dumps(
            {
                "regime": "stagflationary",
                "dominant_signal": "Oil shock via Hormuz closure",
                "regime_30d_call": "Defensive positioning favored",
                "sector_leaders": ["XLE", "XLU"],
                "sector_laggards": ["XLK", "XLRE"],
                "narrative_tags": [
                    {"symbol": "SPY", "tag_key": "macro_shock", "tag_value": "stagflationary"}
                ],
                "risk_level": "high",
            }
        ) + "\n```"
        usage = type("Usage", (), {"input_tokens": 1000, "output_tokens": 500})()

    return FakeResponse()


def fake_chart_response():
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "symbol": "NVDA",
                        "timeframe": "30m",
                        "bias": "bullish",
                        "setup_score": 4,
                        "ema_stack": "bullish_stack",
                        "trend": "uptrend",
                        "rvol_state": "high",
                        "rsi_level": 62.5,
                        "rsi_divergence": "none",
                        "compressed": False,
                        "gap_fills_visible": [],
                        "setup_class": "momentum_continuation",
                        "invalidation_note": "Close below 202.50",
                        "invalidation_price_estimate": 202.5,
                        "warnings": [],
                        "brief": "NVDA showing bullish momentum above 202.5 king node.",
                    }
                ),
            }
        ],
        "usage": {"input_tokens": 500, "output_tokens": 300},
    }


def test_generate_morning_brief_no_api_key():
    result = generate_morning_brief(api_key="")
    assert result.error == "OPENAI_API_KEY not configured"


def test_generate_morning_brief_success(monkeypatch):
    class FakeOpenAI:
        def __init__(self, api_key: str):
            self.api_key = api_key
            self.responses = type("Responses", (), {"create": lambda *args, **kwargs: fake_brief_response()})()

    fake_module = types.SimpleNamespace(OpenAI=FakeOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    result = generate_morning_brief(api_key="test_key")
    assert result.regime == "stagflationary"
    assert result.risk_level == "high"
    assert "XLE" in result.sector_leaders
    assert result.error is None


def test_generate_morning_brief_json_parse_failure(monkeypatch):
    class FakeResponse:
        output_text = "No structured payload included."
        usage = type("Usage", (), {"input_tokens": 1, "output_tokens": 1})()

    class FakeOpenAI:
        def __init__(self, api_key: str):
            self.responses = type("Responses", (), {"create": lambda *args, **kwargs: FakeResponse()})()

    fake_module = types.SimpleNamespace(OpenAI=FakeOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    result = generate_morning_brief(api_key="test_key")
    assert result.error == "json_parse_failed"
    assert result.full_text is not None


def test_analyze_chart_no_api_key():
    result = analyze_chart_from_bytes(b"fake", api_key="")
    assert result.error == "ANTHROPIC_API_KEY not configured"


def test_analyze_chart_success(monkeypatch, tmp_path):
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb1\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    image_path = tmp_path / "test.png"
    image_path.write_bytes(png_bytes)

    class FakeAnthropic:
        def __init__(self, api_key: str):
            self.messages = type("Messages", (), {"create": lambda *args, **kwargs: fake_chart_response()})()

    fake_module = types.SimpleNamespace(Anthropic=FakeAnthropic)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    result = analyze_chart(str(image_path), api_key="test_key")
    assert result.bias == "bullish"
    assert result.setup_score == 4
    assert result.setup_class == "momentum_continuation"
    assert result.error is None


def test_analyze_chart_from_bytes_success(monkeypatch):
    class FakeAnthropic:
        def __init__(self, api_key: str):
            self.messages = type("Messages", (), {"create": lambda *args, **kwargs: fake_chart_response()})()

    fake_module = types.SimpleNamespace(Anthropic=FakeAnthropic)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    result = analyze_chart_from_bytes(
        b"fake_image_bytes",
        media_type="image/png",
        api_key="test_key",
    )
    assert result.symbol == "NVDA"


def test_post_brief_tags_writes_to_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(str(db_path))
    bootstrap_schema(conn, "aion_terminal/storage/schema.sql")

    brief = BriefResult(
        generated_at="2026-04-25T00:00:00Z",
        regime="risk-off",
        dominant_signal="credit spreads widening",
        regime_30d_call="defensive",
        sector_leaders=["XLU"],
        sector_laggards=["XLY"],
        exec_summary="Credit spreads widening signals risk-off regime.",
        narrative_tags=[
            {"symbol": "SPY", "tag_key": "macro_shock", "tag_value": "risk_off"},
            {"symbol": "XLU", "tag_key": "sector_rerating_up", "tag_value": "defensive_bid"},
        ],
        risk_level="high",
        full_text="text",
        model="gpt-5.4-mini",
        tokens_used=100,
    )

    inserted = post_brief_tags(brief, conn)
    rows = conn.execute("SELECT symbol, tag_key FROM manual_narrative_tags").fetchall()
    conn.close()

    assert inserted == 2
    assert len(rows) == 2


def test_macro_brief_system_prompt_contains_ten_sections():
    assert "Macro Regime" in MACRO_BRIEF_SYSTEM_PROMPT
    assert "Liquidity" in MACRO_BRIEF_SYSTEM_PROMPT
    assert "Risk Matrix" in MACRO_BRIEF_SYSTEM_PROMPT
    assert "regime" in MACRO_BRIEF_SYSTEM_PROMPT


def test_chart_system_prompt_contains_scoring_framework():
    assert "setup_score" in CHART_ANALYSIS_SYSTEM_PROMPT
    assert "ema_stack" in CHART_ANALYSIS_SYSTEM_PROMPT
    assert "invalidation" in CHART_ANALYSIS_SYSTEM_PROMPT
