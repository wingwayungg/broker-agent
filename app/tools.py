"""
LangGraph-callable tools. Most tools here are read-only. The one exception
is buy_stock, the only order-placing capability in this project — there is
still no sell_stock, cancel_order, or modify_order tool anywhere. The agent
physically cannot take an action it doesn't have a tool for, regardless of
what the prompt says or what the user asks.

buy_stock's safety isn't a prompt instruction ("please confirm twice") —
it's enforced by the LangGraph engine itself via two interrupt() calls.
interrupt() pauses graph execution mid-tool-call and returns control to
whoever is driving the graph (the browser UI via the LangGraph API, or
ask() in local_api_cli/session.py); nothing after that point in the
function runs until a caller external to the LLM resumes it with
Command(resume=...). The model that decided to call buy_stock never gets
to see or answer its own confirmation prompts — it isn't invoked again
until a real reply comes back from a human. That's what makes this
human-in-the-loop rather than just an LLM being told to ask nicely.
"""
import re
from typing import Annotated

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import interrupt

from app.broker_client import broker_client
from app.rag import EXCERPTS_PER_SEARCH, SectionName
from app.rag import search_filings as _search_filings
from app.research import research_stock as _research_stock


def _is_affirmative(reply: object) -> bool:
    return isinstance(reply, str) and reply.strip().lower() in {
        "y",
        "yes",
        "confirm",
        "confirmed",
    }


@tool
def get_positions() -> str:
    """Get all current portfolio positions with quantity, cost basis,
    current price, and unrealized P&L percentage."""
    positions = broker_client.get_positions()
    lines = []
    for p in positions:
        lines.append(
            f"{p.symbol}: {p.quantity} shares, avg cost ${p.avg_cost:.2f}, "
            f"current ${p.current_price:.2f}, "
            f"unrealized P&L {p.unrealized_pnl_pct:+.1f}%, "
            f"market value ${p.market_value:,.2f}"
        )
    return "\n".join(lines) if lines else "No open positions."


@tool
def get_account_summary() -> str:
    """Get account-level summary: net liquidation value, cash, buying
    power, and unrealized/realized P&L."""
    s = broker_client.get_account_summary()
    return (
        f"Net liquidation: ${s.net_liquidation:,.2f}\n"
        f"Total cash: ${s.total_cash:,.2f}\n"
        f"Buying power: ${s.buying_power:,.2f}\n"
        f"Unrealized P&L: ${s.unrealized_pnl:,.2f}\n"
        f"Realized P&L today: ${s.realized_pnl_today:,.2f}"
    )


@tool
def get_open_orders() -> str:
    """Get all currently open (unfilled) orders."""
    orders = broker_client.get_open_orders()
    if not orders:
        return "No open orders."
    lines = [
        f"Order {o.order_id}: {o.action} {o.quantity} {o.symbol} "
        f"({o.order_type}"
        + (f" @ ${o.limit_price:.2f}" if o.limit_price else "")
        + f") — {o.status}"
        for o in orders
    ]
    return "\n".join(lines)


@tool
def get_recent_fills() -> str:
    """Get recently filled orders (executions)."""
    fills = broker_client.get_recent_fills()
    if not fills:
        return "No recent fills."
    lines = [
        f"{f.timestamp}: {f.action} {f.quantity} {f.symbol} @ ${f.price:.2f}"
        for f in fills
    ]
    return "\n".join(lines)


@tool
def get_bracket_order_status(symbol: str) -> str:
    """Get the status of a bracket order (parent, take-profit, and
    stop-loss legs) for a given symbol."""
    status = broker_client.get_bracket_order_status(symbol)
    return (
        f"{status.symbol} bracket order — "
        f"parent: {status.parent_status}, "
        f"take-profit: {status.take_profit_status or 'n/a'}, "
        f"stop-loss: {status.stop_loss_status or 'n/a'}"
    )


@tool
def research_stock(symbol: str) -> str:
    """Research a stock — not necessarily one you hold — by fanning out to
    up to three specialized sub-agents (fundamentals, technicals, and
    news/sentiment) and returning a three-paragraph informational summary.
    This is general research, not personalized investment advice."""
    return _research_stock(symbol)


# The start of each excerpt's citation line in format_hits' output.
_EXCERPT_NUMBER = re.compile(r"^\[(\d+)\] \S+ 10-K FY\d{4} · ", re.MULTILINE)


def _first_excerpt_number(messages: list, tool_call_id: str) -> int:
    """Where this search's excerpt numbering starts, so every number in a
    thread names one excerpt. Numbering from [1] on every call meant a
    year-over-year answer had two [1]s, and its Sources list couldn't say
    which filing a claim came from. Parallel calls in one AI message run
    against the same state without seeing each other's results, so each
    takes its own block of EXCERPTS_PER_SEARCH numbers after the thread's
    highest."""
    used = [
        int(n)
        for m in messages
        if m.type == "tool" and m.name == "search_filings" and isinstance(m.content, str)
        for n in _EXCERPT_NUMBER.findall(m.content)
    ]
    start = max(used, default=0) + 1
    last = messages[-1] if messages else None
    siblings = [c["id"] for c in getattr(last, "tool_calls", None) or [] if c["name"] == "search_filings"]
    if tool_call_id in siblings:
        start += siblings.index(tool_call_id) * EXCERPTS_PER_SEARCH
    return start


@tool
def search_filings(
    symbol: str,
    query: str,
    messages: Annotated[list, InjectedState("messages")],
    tool_call_id: Annotated[str, InjectedToolCallId],
    fiscal_year: int | None = None,
    section: SectionName | None = None,
) -> str:
    """Search a company's own annual reports (its two most recent SEC 10-K
    filings) and return numbered excerpts with citations. Use this for
    questions about what a company itself discloses: its business and
    segments, risk factors, management's discussion of results, or market
    risk — and for how that disclosure changed between years. `query` is
    what to look for, in plain words. `fiscal_year` (the company's own
    fiscal-year label) defaults to the most recent filing; pass the older
    year explicitly to search it. Optionally narrow by `section` (business,
    risk_factors, mdna, market_risk). The first search for a company takes
    several seconds while its filings are downloaded and indexed."""
    return _search_filings(
        symbol,
        query,
        fiscal_year=fiscal_year,
        section=section,
        first_number=_first_excerpt_number(messages, tool_call_id),
    )


@tool
def buy_stock(symbol: str, quantity: float) -> str:
    """Buy shares of a stock. This is the only order-placing tool in the
    whole project — there is no sell/cancel/modify equivalent. It requires
    two separate explicit human confirmations before anything is submitted;
    that gate is enforced by the graph, not by this prompt, so calling this
    tool does not itself place an order — it starts a confirmation sequence
    that only completes if a real human replies affirmatively twice.
    Call this as soon as the user gives an explicit buy instruction with
    both a symbol and a quantity — do not pre-confirm with the user in text
    first, the tool's own confirmation steps handle that."""
    symbol = symbol.strip().upper()
    if quantity <= 0:
        return "Quantity must be a positive number of shares."

    quote = broker_client.get_quote(symbol)
    estimated_cost = round(quote * quantity, 2)
    account = broker_client.get_account_summary()
    if estimated_cost > account.buying_power:
        return (
            f"Cannot place order: estimated cost ${estimated_cost:,.2f} for "
            f"{quantity} {symbol} @ ~${quote:.2f} exceeds available buying "
            f"power of ${account.buying_power:,.2f}."
        )

    first_reply = interrupt(
        {
            "type": "confirm_buy_order",
            "step": 1,
            "of": 2,
            "message": (
                f"Confirm order: BUY {quantity} {symbol} @ ~${quote:.2f} "
                f"(estimated cost ${estimated_cost:,.2f}). Reply 'yes' to "
                f"continue or 'no' to cancel."
            ),
            "symbol": symbol,
            "quantity": quantity,
            "estimated_price": quote,
            "estimated_cost": estimated_cost,
        }
    )
    if not _is_affirmative(first_reply):
        return "Order cancelled — first confirmation was not a clear yes."

    second_reply = interrupt(
        {
            "type": "confirm_buy_order",
            "step": 2,
            "of": 2,
            "message": (
                f"Final confirmation — this will submit a real order: BUY "
                f"{quantity} {symbol} @ ~${quote:.2f} (estimated cost "
                f"${estimated_cost:,.2f}). This cannot be undone once "
                f"submitted. Reply 'yes' to submit or 'no' to cancel."
            ),
            "symbol": symbol,
            "quantity": quantity,
        }
    )
    if not _is_affirmative(second_reply):
        return "Order cancelled at final confirmation — nothing was submitted."

    order = broker_client.place_order(symbol=symbol, quantity=quantity, action="BUY")
    return (
        f"Order submitted: BUY {quantity} {symbol} @ ~${quote:.2f} "
        f"(order id {order.order_id}, status {order.status})."
    )


ALL_TOOLS = [
    get_positions,
    get_account_summary,
    get_open_orders,
    get_recent_fills,
    get_bracket_order_status,
    research_stock,
    search_filings,
    buy_stock,
]


# Tools whose results are long and already relayed in the agent's own reply:
# search_filings returns ~2k tokens of excerpts per call, which the reply
# quotes and cites; research_stock's summary is presented near-verbatim.
# Groq's free tier caps gpt-oss-120b at 8k tokens per minute, so carrying
# every earlier turn's excerpts into every later request first throttles a
# thread and then fails it outright with a 413. Broker tool results are
# short and are what follow-ups like "which of those are up 5%?" resolve
# against, so they're kept whole.
_COMPACTED_TOOLS = {search_filings.name, research_stock.name}


def compact_earlier_tool_results(messages: list) -> list:
    """The message list to send the LLM: bulky tool results from turns
    before the latest user message are replaced with a stub. The current
    turn's results stay whole, and the stub keeps each ToolMessage (and its
    tool_call_id) in place, since providers reject a tool call with no
    matching result. Only the copy sent to the LLM changes — the checkpointed
    thread still holds the full results."""
    last_user = max((i for i, m in enumerate(messages) if m.type == "human"), default=-1)
    return [
        m.model_copy(
            update={
                "content": f"[{m.name} result from an earlier turn omitted; "
                "call the tool again if you need it]"
            }
        )
        if i < last_user and m.type == "tool" and m.name in _COMPACTED_TOOLS
        else m
        for i, m in enumerate(messages)
    ]


# gpt-oss cites in its built-in browsing tool's format, 【2†L1-L5】, even
# though the system prompt asks for [2]. The line range doesn't refer to
# anything in search_filings' excerpts, and the frontend would show the
# raw markers, so they're rewritten to the [n] the Sources list uses.
# app/static/app.js applies the same rewrite to the reply while it streams.
_OSS_CITATION = re.compile(r"【(\d+)(?:†[^】]*)?】")


def normalize_citations(text: str) -> str:
    return _OSS_CITATION.sub(r"[\1]", text)
