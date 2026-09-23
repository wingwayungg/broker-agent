"""
Tests against the mock broker client. These should keep passing unmodified
once you wire up the real client, as long as get_positions() etc. still
return the Pydantic models defined in app/models.py — that's the whole
point of the contract.
"""
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, MessagesState
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from app.tools import (
    ALL_TOOLS,
    buy_stock,
    get_account_summary,
    get_bracket_order_status,
    get_open_orders,
    get_positions,
    get_recent_fills,
    research_stock,
)


@pytest.fixture(autouse=True)
def _no_live_price_lookups():
    """These are unit tests against the mock broker client, not integration
    tests against Yahoo Finance — keep them offline and fast by always
    falling back to the static mock quotes."""
    with patch("app.broker_client.fetch_current_price", return_value=None):
        yield


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
    with patch("app.research.get_llm", return_value=_FakeLLM()), patch(
        "app.research.fetch_company_name", return_value="Apple Inc."
    ), patch(
        "app.research.fetch_price_snapshot", return_value="mock price snapshot"
    ), patch(
        "app.research.fetch_web_context", return_value="mock web context"
    ) as mock_web:
        result = research_stock.invoke({"symbol": "aapl"})
    paragraphs = [p for p in result.split("\n\n") if p.strip()]
    assert len(paragraphs) == 3

    # Both web-backed researchers must search by company name, not just the
    # ticker, and fundamentals must not be restricted to a recent-news window.
    # Keyed on the stable topic word so rewording a query doesn't break this.
    calls = {c.args[1]: c.kwargs for c in mock_web.call_args_list}
    assert all(kw["company_name"] == "Apple Inc." for kw in calls.values())
    fundamentals = next(kw for q, kw in calls.items() if "earnings" in q)
    news = next(kw for q, kw in calls.items() if "news" in q)
    assert fundamentals["recent_days"] is None
    assert "recent_days" not in news


def test_buy_stock_is_the_only_order_placing_tool():
    assert buy_stock in ALL_TOOLS
    assert not any(t.name in {"sell_stock", "cancel_order", "modify_order"} for t in ALL_TOOLS)


def _build_tool_graph():
    """A minimal graph (no LLM) so buy_stock's interrupt()/resume flow can
    be tested against the real LangGraph engine, not a mock of it."""
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([buy_stock]))
    graph.set_entry_point("tools")
    graph.set_finish_point("tools")
    return graph.compile(checkpointer=MemorySaver())


def _buy_call(symbol: str, quantity: float) -> dict:
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "buy_stock", "args": {"symbol": symbol, "quantity": quantity}, "id": "call1"}
                ],
            )
        ]
    }


def test_buy_stock_requires_two_affirmative_confirmations_before_submitting():
    graph = _build_tool_graph()
    config = {"configurable": {"thread_id": "buy-1"}}

    result = graph.invoke(_buy_call("AAPL", 10), config=config)
    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value["step"] == 1

    result = graph.invoke(Command(resume="yes"), config=config)
    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value["step"] == 2

    result = graph.invoke(Command(resume="yes"), config=config)
    final = result["messages"][-1].content
    assert "Order submitted" in final
    assert "AAPL" in final


def test_buy_stock_aborts_without_placing_order_on_first_no():
    graph = _build_tool_graph()
    config = {"configurable": {"thread_id": "buy-2"}}

    graph.invoke(_buy_call("AAPL", 10), config=config)
    result = graph.invoke(Command(resume="no"), config=config)
    final = result["messages"][-1].content
    assert "cancelled" in final.lower()
    assert "Order submitted" not in final


def test_buy_stock_aborts_without_placing_order_on_second_no():
    graph = _build_tool_graph()
    config = {"configurable": {"thread_id": "buy-3"}}

    graph.invoke(_buy_call("AAPL", 10), config=config)
    graph.invoke(Command(resume="yes"), config=config)
    result = graph.invoke(Command(resume="no"), config=config)
    final = result["messages"][-1].content
    assert "cancelled" in final.lower()
    assert "Order submitted" not in final


def test_buy_stock_rejects_order_exceeding_buying_power_before_any_confirmation():
    graph = _build_tool_graph()
    config = {"configurable": {"thread_id": "buy-4"}}

    result = graph.invoke(_buy_call("AAPL", 100_000), config=config)
    assert "__interrupt__" not in result
    final = result["messages"][-1].content
    assert "exceeds available buying power" in final
