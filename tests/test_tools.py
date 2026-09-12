"""
Tests against the mock IBKR client. These should keep passing unmodified
once you wire up the real client, as long as get_positions() etc. still
return the Pydantic models defined in app/models.py — that's the whole
point of the contract.
"""
from app.tools import (
    get_account_summary,
    get_bracket_order_status,
    get_open_orders,
    get_positions,
    get_recent_fills,
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
