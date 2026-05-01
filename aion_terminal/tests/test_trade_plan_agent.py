from pathlib import Path
import types
import sys

from aion_terminal.agents.prompts import TRADE_PLAN_SYSTEM_PROMPT_V3
from aion_terminal.agents.trade_plan_agent import generate_trade_plan


class _Usage:
    input_tokens = 10
    output_tokens = 20


class _Resp:
    usage = _Usage()
    output_text = """
TRADE PLAN NARRATIVE
Decision: CONDITIONAL TRADE
JSON_PLAN:
{"decision":"conditional_trade","symbol":"META","bias":"bullish","decision_engine":{}}
"""


def fake_trade_plan_response_success():
    return _Resp()


def test_generate_trade_plan_no_api_key():
    out = generate_trade_plan(symbol="META", api_key="")
    assert out.error == "OPENAI_API_KEY not configured"


def test_generate_trade_plan_success(monkeypatch):
    class Client:
        def __init__(self, api_key=None):
            self.responses = types.SimpleNamespace(create=lambda **kwargs: fake_trade_plan_response_success())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=Client))
    out = generate_trade_plan(symbol="META", api_key="test", rankings_payload={"spot": 100})
    assert out.symbol == "META"
    assert out.error is None
    assert out.json_plan["decision"] == "conditional_trade"
    assert out.decision_engine is not None


def test_generate_trade_plan_json_parse_failure(monkeypatch):
    class BadResp:
        usage = _Usage()
        output_text = "TRADE PLAN NARRATIVE only"

    class Client:
        def __init__(self, api_key=None):
            self.responses = types.SimpleNamespace(create=lambda **kwargs: BadResp())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=Client))
    out = generate_trade_plan(symbol="META", api_key="test", rankings_payload={"spot": 100})
    assert out.error == "json_parse_failed"
    assert out.narrative is not None


def test_trade_plan_system_prompt_contains_required_sections():
    assert "S1 / S2 / S3" in TRADE_PLAN_SYSTEM_PROMPT_V3
    assert "STRUCTURAL EXIT FRAMEWORK" in TRADE_PLAN_SYSTEM_PROMPT_V3
    assert "TIME-OF-DAY FILTER" in TRADE_PLAN_SYSTEM_PROMPT_V3
    assert "JSON_PLAN" in TRADE_PLAN_SYSTEM_PROMPT_V3


def test_trade_plan_route_payload_model_defaults_if_added():
    text = Path("aion_terminal/api/routes_agents.py").read_text(encoding="utf-8")
    assert "account_buying_power: float | None = 5000" in text
    assert "cash_account: bool = True" in text
    assert "Field(default_factory=list)" in text
