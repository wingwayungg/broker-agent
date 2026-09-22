"""
Tests for app/market_data.py — the live-data fetchers research_stock relies
on instead of the LLM's training memory. requests.get/post are mocked so
these run offline and don't depend on Yahoo/Tavily being reachable.
"""
from unittest.mock import MagicMock, patch

from app.market_data import fetch_company_name, fetch_price_snapshot, fetch_web_context


def _yahoo_response(meta: dict, closes: list) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "chart": {
            "result": [
                {
                    "meta": meta,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }
    return resp


def test_fetch_price_snapshot_happy_path():
    meta = {
        "regularMarketPrice": 330.0,
        "regularMarketDayLow": 328.0,
        "regularMarketDayHigh": 332.0,
        "fiftyTwoWeekLow": 243.42,
        "fiftyTwoWeekHigh": 344.57,
        "regularMarketVolume": 86588203,
        # Deliberately far from the real previous close, to prove the code
        # doesn't use this despite the misleading name (see market_data.py).
        "chartPreviousClose": 999.0,
    }
    closes = [300.0, 305.0, 310.0, None, 320.0, 315.5, 330.0]

    with patch("app.market_data.requests.get", return_value=_yahoo_response(meta, closes)):
        result = fetch_price_snapshot("AAPL")

    assert "Current price: $330.00" in result
    # prev_close must come from closes[-2] (315.5), not chartPreviousClose.
    assert f"Change vs previous close: {(330.0 - 315.5) / 315.5 * 100:+.2f}%" in result
    assert "Day range: $328.00 - $332.00" in result
    assert "52-week range: $243.42 - $344.57" in result
    assert "Volume: 86,588,203" in result
    filtered = [300.0, 305.0, 310.0, 320.0, 315.5, 330.0]
    assert f"~1-month change: {(filtered[-1] - filtered[0]) / filtered[0] * 100:+.2f}%" in result


def test_fetch_price_snapshot_omits_lines_for_missing_optional_fields():
    meta = {"regularMarketPrice": 330.0}
    closes = [330.0]

    with patch("app.market_data.requests.get", return_value=_yahoo_response(meta, closes)):
        result = fetch_price_snapshot("AAPL")

    assert result == "Current price: $330.00"


def test_fetch_price_snapshot_handles_request_failure():
    with patch("app.market_data.requests.get", side_effect=ConnectionError("boom")):
        result = fetch_price_snapshot("AAPL")

    assert result == "[price data unavailable: boom]"


def test_fetch_price_snapshot_handles_missing_price():
    meta = {}
    closes = []

    with patch("app.market_data.requests.get", return_value=_yahoo_response(meta, closes)):
        result = fetch_price_snapshot("AAPL")

    assert result == "Current price: unavailable"


def test_fetch_company_name_prefers_long_name():
    meta = {"shortName": "Everpure", "longName": "Everpure, Inc."}
    with patch("app.market_data.requests.get", return_value=_yahoo_response(meta, [])):
        assert fetch_company_name("P") == "Everpure, Inc."


def test_fetch_company_name_falls_back_to_short_name():
    with patch("app.market_data.requests.get", return_value=_yahoo_response({"shortName": "Everpure"}, [])):
        assert fetch_company_name("P") == "Everpure"


def test_fetch_company_name_returns_none_when_unavailable():
    with patch("app.market_data.requests.get", side_effect=ConnectionError("boom")):
        assert fetch_company_name("P") is None
    with patch("app.market_data.requests.get", return_value=_yahoo_response({}, [])):
        assert fetch_company_name("P") is None


def _tavily_response(results: list) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"results": results}
    return resp


def test_fetch_web_context_returns_message_when_no_api_key_configured():
    with patch("app.market_data.settings.tavily_api_key", ""), patch(
        "app.market_data.requests.post"
    ) as mock_post:
        result = fetch_web_context("AAPL", "recent news")

    assert result == "[web search unavailable: no TAVILY_API_KEY configured]"
    mock_post.assert_not_called()


def test_fetch_web_context_happy_path_formats_and_truncates_results():
    long_content = "x" * 600
    results = [
        {"title": "Apple Q3 earnings", "url": "https://example.com/a", "content": long_content},
        {"title": "Apple news", "url": "https://example.com/b", "content": "short update"},
    ]

    with patch("app.market_data.settings.tavily_api_key", "fake-key"), patch(
        "app.market_data.requests.post", return_value=_tavily_response(results)
    ) as mock_post:
        result = fetch_web_context("AAPL", "latest quarterly earnings")

    assert "- Apple Q3 earnings (https://example.com/a):" in result
    assert "x" * 500 in result
    assert "x" * 501 not in result
    assert "- Apple news (https://example.com/b):\nshort update" in result

    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["query"] == "AAPL stock latest quarterly earnings"
    assert call_kwargs["json"]["api_key"] == "fake-key"
    # Default is the news index over the last two weeks.
    assert call_kwargs["json"]["topic"] == "news"
    assert call_kwargs["json"]["days"] == 14


def test_fetch_web_context_anchors_query_on_company_name():
    # A bare one-letter ticker like "P" is useless as a search term; the
    # company name must be in the query so results are about the right stock.
    with patch("app.market_data.settings.tavily_api_key", "fake-key"), patch(
        "app.market_data.requests.post", return_value=_tavily_response([])
    ) as mock_post:
        fetch_web_context("P", "recent news and catalysts", company_name="Everpure, Inc.")

    query = mock_post.call_args.kwargs["json"]["query"]
    assert query == "Everpure, Inc. (P) stock recent news and catalysts"


def test_fetch_web_context_without_window_searches_general_index():
    with patch("app.market_data.settings.tavily_api_key", "fake-key"), patch(
        "app.market_data.requests.post", return_value=_tavily_response([])
    ) as mock_post:
        fetch_web_context("AAPL", "latest quarterly earnings", recent_days=None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["topic"] == "general"
    assert "days" not in payload


def test_fetch_web_context_returns_message_when_no_results():
    with patch("app.market_data.settings.tavily_api_key", "fake-key"), patch(
        "app.market_data.requests.post", return_value=_tavily_response([])
    ):
        result = fetch_web_context("AAPL", "recent news")

    assert result == "[no recent web results found]"


def test_fetch_web_context_handles_request_failure():
    with patch("app.market_data.settings.tavily_api_key", "fake-key"), patch(
        "app.market_data.requests.post", side_effect=TimeoutError("timed out")
    ):
        result = fetch_web_context("AAPL", "recent news")

    assert result == "[web search unavailable: timed out]"
