"""
Tests for app/broker_client.py's live-price fallback logic — kept separate
from tests/test_tools.py (which mocks fetch_current_price away entirely)
so these can exercise the fallback behavior directly.
"""
from unittest.mock import patch

from app.broker_client import _MOCK_QUOTES, _live_or_fallback_price, broker_client


def test_live_or_fallback_price_uses_live_price_when_available():
    with patch("app.broker_client.fetch_current_price", return_value=205.50):
        assert _live_or_fallback_price("AAPL") == 205.50


def test_live_or_fallback_price_preserves_a_legitimate_zero_price():
    # A halted/delisted symbol can legitimately quote at 0.0 — that's a
    # real value, not a failed fetch, and must not be overridden.
    with patch("app.broker_client.fetch_current_price", return_value=0.0):
        assert _live_or_fallback_price("AAPL") == 0.0


def test_live_or_fallback_price_falls_back_to_mock_quote_when_fetch_fails():
    with patch("app.broker_client.fetch_current_price", return_value=None):
        assert _live_or_fallback_price("AAPL") == _MOCK_QUOTES["AAPL"]


def test_live_or_fallback_price_falls_back_to_default_for_unknown_symbol():
    with patch("app.broker_client.fetch_current_price", return_value=None):
        assert _live_or_fallback_price("ZZZZ") == 100.00


def test_get_positions_assigns_each_symbol_its_own_price():
    prices = {"AAPL": 205.50, "NVDA": 140.25, "MSFT": 410.00}
    with patch("app.broker_client.fetch_current_price", side_effect=lambda s: prices[s]):
        positions = broker_client.get_positions()

    by_symbol = {p.symbol: p.current_price for p in positions}
    assert by_symbol == prices
