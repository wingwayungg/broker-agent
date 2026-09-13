"""
Stock research via sub-agents.

Separate from app/tools.py's IBKR tools, which read the user's own account.
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

_RESEARCHERS = {
    "fundamentals": (
        "You are a fundamentals research analyst. Given a stock ticker, "
        "summarize what you know about the company's business model, recent "
        "revenue/earnings trends, margins, and valuation, in 3-4 sentences. "
        "State clearly when you're unsure of exact recent figures rather "
        "than inventing numbers."
    ),
    "technicals": (
        "You are a technical/price-action analyst. Given a stock ticker, "
        "summarize its general recent price trend, volatility, and any "
        "well-known support/resistance behavior, in 3-4 sentences. State "
        "clearly when you don't have current price data rather than "
        "inventing numbers."
    ),
    "news_sentiment": (
        "You are a news and sentiment analyst. Given a stock ticker, "
        "summarize recent notable news, catalysts, or shifts in analyst or "
        "market sentiment you're aware of, in 3-4 sentences. State clearly "
        "when you don't have up-to-date news rather than inventing events."
    ),
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
    response = get_llm().invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=f"Ticker: {symbol}")]
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
