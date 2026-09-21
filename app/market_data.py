"""
Live market data helpers for app/research.py.

An LLM's training data goes stale (prices, earnings, news are all wrong a
few months after the training cutoff), so the researcher sub-agents in
app/research.py need real, current data pulled at call time rather than
relying on what the model "remembers" about a ticker. Two free sources:

- Yahoo Finance's unofficial chart endpoint, for technicals (price, day
  range, 52-week range, volume, recent trend). It's undocumented and
  unsupported by Yahoo, but works without an API key. Yahoo's newer
  quote/quoteSummary endpoints (which would give fundamentals like P/E and
  market cap) now require a session crumb we don't have, so they're not
  used here.
- Tavily web search, for anything the chart endpoint can't provide:
  news/sentiment, and fundamentals numbers like recent revenue/earnings/
  margins, which are only reliably found in current web content anyway.
"""
import requests

from app.config import settings

_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _fetch_chart_data(symbol: str) -> tuple[dict, list[float]]:
    """Raw meta + daily closes for a symbol from Yahoo Finance's chart
    endpoint. Raises on any failure; callers decide how to degrade."""
    resp = requests.get(
        _YAHOO_CHART_URL.format(symbol=symbol),
        params={"interval": "1d", "range": "1mo"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    meta = result["meta"]
    closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
    return meta, closes


def fetch_current_price(symbol: str) -> float | None:
    """Latest traded price for a symbol, or None if it couldn't be fetched.
    Used to keep position/quote prices live instead of stale mock values."""
    try:
        meta, _ = _fetch_chart_data(symbol)
    except Exception:
        return None
    return meta.get("regularMarketPrice")


def fetch_price_snapshot(symbol: str) -> str:
    """Current price, day range, 52-week range, volume, and ~1-month trend
    for a symbol, from Yahoo Finance's chart endpoint. Returns a plain-text
    summary, or a bracketed explanation if the data couldn't be fetched."""
    try:
        meta, closes = _fetch_chart_data(symbol)
    except Exception as exc:
        return f"[price data unavailable: {exc}]"

    price = meta.get("regularMarketPrice")
    # meta["chartPreviousClose"] is the close at the *start* of the requested
    # range (e.g. ~1 month ago), not yesterday's close, despite the name —
    # the actual previous trading day's close is the second-to-last daily
    # close in the series.
    prev_close = closes[-2] if len(closes) >= 2 else None

    lines = [
        f"Current price: ${price:.2f}" if price is not None else "Current price: unavailable"
    ]
    if price is not None and prev_close:
        lines.append(f"Change vs previous close: {(price - prev_close) / prev_close * 100:+.2f}%")
    if meta.get("regularMarketDayLow") and meta.get("regularMarketDayHigh"):
        lines.append(
            f"Day range: ${meta['regularMarketDayLow']:.2f} - ${meta['regularMarketDayHigh']:.2f}"
        )
    if meta.get("fiftyTwoWeekLow") and meta.get("fiftyTwoWeekHigh"):
        lines.append(
            f"52-week range: ${meta['fiftyTwoWeekLow']:.2f} - ${meta['fiftyTwoWeekHigh']:.2f}"
        )
    if meta.get("regularMarketVolume"):
        lines.append(f"Volume: {meta['regularMarketVolume']:,}")
    if len(closes) >= 2 and closes[0]:
        lines.append(f"~1-month change: {(closes[-1] - closes[0]) / closes[0] * 100:+.2f}%")

    return "\n".join(lines)


def fetch_web_context(symbol: str, topic: str) -> str:
    """Recent web search results for a symbol via Tavily, scoped by topic
    (e.g. "recent news and catalysts" or "latest quarterly earnings, revenue,
    and margins"). Returns concatenated result snippets, or a bracketed
    explanation if search failed or no key is configured."""
    if not settings.tavily_api_key:
        return "[web search unavailable: no TAVILY_API_KEY configured]"
    try:
        resp = requests.post(
            _TAVILY_SEARCH_URL,
            json={
                "api_key": settings.tavily_api_key,
                "query": f"{symbol} stock {topic}",
                "search_depth": "basic",
                "topic": "news",
                "max_results": 5,
                "days": 14,
            },
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as exc:
        return f"[web search unavailable: {exc}]"

    if not results:
        return "[no recent web results found]"

    return "\n\n".join(
        f"- {r.get('title', 'untitled')} ({r.get('url', '')}):\n"
        f"{r.get('content', '').strip()[:500]}"
        for r in results
    )
