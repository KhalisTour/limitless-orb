from pathlib import Path
import types
import sys
import asyncio
from types import SimpleNamespace

if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None)

from aion_terminal.agents.prompts import TRADE_PLAN_SYSTEM_PROMPT_V3
from aion_terminal.agents.trade_plan_agent import generate_trade_plan
from aion_terminal.api import routes_agents


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


def test_trade_plan_route_wires_rankings_and_contracts(monkeypatch):
    captured: dict = {}

    ranking = SimpleNamespace(
        symbol="META",
        spot=515.0,
        call_wall=525.0,
        put_wall=500.0,
        king_node=510.0,
        signals=[{"setup_name": "s2_pullback", "bias": "bullish", "confidence_raw": 0.66}],
        best_contract={
            "contract_symbol": "META260515C00515000",
            "expiry": "2026-05-15",
            "strike": 515.0,
            "premium_mid": 8.2,
            "bid": 8.1,
            "ask": 8.3,
            "delta": 0.44,
            "gamma": 0.06,
            "theta": -0.12,
            "open_interest": 1000,
            "volume": 220,
        },
        safer_contract=None,
        convex_contract=None,
    )

    monkeypatch.setattr(routes_agents, "rank_symbol", lambda *_args, **_kwargs: ranking)

    def fake_generate_trade_plan(*args, **kwargs):
        captured["rankings_payload"] = args[7]
        captured["contract_recommendations"] = args[8]
        return SimpleNamespace(
            generated_at="2026-05-01T00:00:00Z",
            symbol="META",
            decision="conditional_trade",
            bias="bullish",
            confidence=0.6,
            confidence_label="medium",
            narrative="ok",
            json_plan={},
            decision_engine={},
            model="gpt-5.4-mini",
            tokens_used=123,
            error=None,
        )

    monkeypatch.setattr(routes_agents, "generate_trade_plan", fake_generate_trade_plan)

    req = routes_agents.TradePlanRequest(symbol="META")
    asyncio.run(routes_agents.create_trade_plan(req))

    assert captured["rankings_payload"]["top_ranked_setup"]["setup_name"] == "s2_pullback"
    best = captured["contract_recommendations"]["best"]
    assert best["contract_symbol"] == "META260515C00515000"
    assert best["expiry"] == "2026-05-15"
    assert best["strike"] == 515.0
    assert best["delta"] == 0.44
    assert best["gamma"] == 0.06
    assert best["theta"] == -0.12
    assert best["bid"] == 8.1
    assert best["ask"] == 8.3
    assert best["mid"] == 8.2
    assert best["oi"] == 1000
    assert best["volume"] == 220
