"""
Tests against the mock IBKR client. These should keep passing unmodified
once you wire up the real client, as long as get_positions() etc. still
return the Pydantic models defined in app/models.py — that's the whole
point of the contract.
"""
from unittest.mock import patch

from app.tools import (
    get_account_summary,
    get_bracket_order_status,
    get_open_orders,
    get_positions,
    get_recent_fills,
    research_stock,
)


def test_get_positions_returns_expected_symbols():
    result = get_positions.invoke({})
    assert "AAPL" in result
    assert "NVDA" in result
    assert "MSFT" in result


def test_get_account_summary_includes_net_liquidation():
    result = get_account_summary.invoke({})
    assert "Net liquidation" in result


def test_get_open_orders_lists_tsla_order():
    result = get_open_orders.invoke({})
    assert "TSLA" in result
    assert "Submitted" in result


def test_get_recent_fills_lists_nvda_fill():
    result = get_recent_fills.invoke({})
    assert "NVDA" in result


def test_get_bracket_order_status_returns_symbol():
    result = get_bracket_order_status.invoke({"symbol": "NVDA"})
    assert "NVDA" in result
    assert "parent" in result.lower()


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def invoke(self, messages):
        # Sub-agent calls carry the ticker in the human message; the final
        # synthesis call carries "Analyst notes" instead.
        last = messages[-1].content
        if "Analyst notes" in last:
            return _FakeResponse("Para 1.\n\nPara 2.\n\nPara 3.")
        return _FakeResponse("mock analyst note")


def test_research_stock_returns_three_paragraph_summary():
    with patch("app.research.get_llm", return_value=_FakeLLM()):
        result = research_stock.invoke({"symbol": "aapl"})
    paragraphs = [p for p in result.split("\n\n") if p.strip()]
    assert len(paragraphs) == 3
