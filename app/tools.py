"""
LangGraph-callable tools. Most tools here are read-only. The one exception
is buy_stock, the only order-placing capability in this project — there is
still no sell_stock, cancel_order, or modify_order tool anywhere. The agent
physically cannot take an action it doesn't have a tool for, regardless of
what the prompt says or what the user asks.

buy_stock's safety isn't a prompt instruction ("please confirm twice") —
it's enforced by the LangGraph engine itself via two interrupt() calls.
interrupt() pauses graph execution mid-tool-call and returns control to
whoever is driving the graph (see ask() in app/agent.py); nothing after
that point in the function runs until a caller external to the LLM resumes
it with Command(resume=...). The model that decided to call buy_stock never
gets to see or answer its own confirmation prompts — it isn't invoked again
until a real reply comes back through ask(). That's what makes this
human-in-the-loop rather than just an LLM being told to ask nicely.
"""
from langchain_core.tools import tool
from langgraph.types import interrupt

from app.broker_client import broker_client
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
    buy_stock,
]
