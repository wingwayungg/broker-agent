"""
Stock research via sub-agents.

Separate from app/tools.py's broker tools, which read the user's own account.
This module answers "tell me about TICKER" style questions by fanning out to
up to three specialized researcher sub-agents (fundamentals, technicals,
news/sentiment) run concurrently, then a synthesis pass condenses their notes
into a single three-paragraph summary. Each sub-agent is a narrow, single-
purpose LLM call rather than the account agent trying to cover every angle
in one broad prompt.

This is informational only — sub-agents are instructed not to issue buy/sell
recommendations, consistent with the account agent's no-advice rule.
"""
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_llm
from app.market_data import fetch_price_snapshot, fetch_web_context

_RESEARCHERS = {
    "fundamentals": (
        "You are a fundamentals research analyst. Given a stock ticker and "
        "live web search results about its recent earnings, revenue, and "
        "margins, summarize the company's business model and how those "
        "figures look, in 3-4 sentences. Base numbers strictly on the "
        "supplied data — if it doesn't cover something, say so rather than "
        "filling the gap from memory."
    ),
    "technicals": (
        "You are a technical/price-action analyst. Given a stock ticker and "
        "a live price snapshot (current price, day range, 52-week range, "
        "volume, recent trend), summarize the current price picture in 3-4 "
        "sentences. Base numbers strictly on the supplied snapshot — if it's "
        "marked unavailable, say so rather than filling the gap from memory."
    ),
    "news_sentiment": (
        "You are a news and sentiment analyst. Given a stock ticker and "
        "live web search results about recent news, summarize notable "
        "catalysts or shifts in sentiment in 3-4 sentences. Base this "
        "strictly on the supplied results — if none were found, say so "
        "rather than inventing events."
    ),
}

_CONTEXT_FETCHERS = {
    "fundamentals": lambda symbol: fetch_web_context(
        symbol, "latest quarterly earnings, revenue, and profit margins"
    ),
    "technicals": lambda symbol: fetch_price_snapshot(symbol),
    "news_sentiment": lambda symbol: fetch_web_context(symbol, "recent news and catalysts"),
}

_SYNTHESIS_PROMPT = (
    "You are combining research notes from three analysts (fundamentals, "
    "technicals, news/sentiment) about a single stock into one summary for "
    "a portfolio holder. Write exactly three paragraphs: paragraph 1 covers "
    "fundamentals, paragraph 2 covers technicals/price action, paragraph 3 "
    "covers news/sentiment and ties it together. Be factual and neutral — "
    "this is informational context, not a buy/sell/hold recommendation, and "
    "you must not tell the user what to do with their position."
)


def _run_researcher(role: str, system_prompt: str, symbol: str) -> str:
    live_data = _CONTEXT_FETCHERS[role](symbol)
    human_content = f"Ticker: {symbol}\n\nLive data pulled just now:\n{live_data}"
    response = get_llm().invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=human_content)]
    )
    return f"[{role}]\n{response.content}"


def research_stock(symbol: str) -> str:
    """Run up to three researcher sub-agents in parallel, then synthesize
    their notes into a three-paragraph summary."""
    symbol = symbol.strip().upper()

    with ThreadPoolExecutor(max_workers=len(_RESEARCHERS)) as pool:
        futures = [
            pool.submit(_run_researcher, role, prompt, symbol)
            for role, prompt in _RESEARCHERS.items()
        ]
        notes = [f.result() for f in futures]

    combined = get_llm().invoke(
        [
            SystemMessage(content=_SYNTHESIS_PROMPT),
            HumanMessage(
                content=f"Ticker: {symbol}\n\nAnalyst notes:\n\n" + "\n\n".join(notes)
            ),
        ]
    )
    return combined.content
