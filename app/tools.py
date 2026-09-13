"""
LangGraph-callable tools. Every tool here is read-only — there is no
place_order, cancel_order, or modify_order tool defined anywhere in this
project. That's a deliberate design choice, not an oversight: the agent
physically cannot take an action it doesn't have a tool for, regardless of
what the prompt says or what the user asks. Enforce safety in code, not
just in instructions.
"""
from langchain_core.tools import tool

from app.ibkr_client import ibkr_client
from app.research import research_stock as _research_stock


@tool
def get_positions() -> str:
    """Get all current portfolio positions with quantity, cost basis,
    current price, and unrealized P&L percentage."""
    positions = ibkr_client.get_positions()
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
    s = ibkr_client.get_account_summary()
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
    orders = ibkr_client.get_open_orders()
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
    fills = ibkr_client.get_recent_fills()
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
    status = ibkr_client.get_bracket_order_status(symbol)
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


ALL_TOOLS = [
    get_positions,
    get_account_summary,
    get_open_orders,
    get_recent_fills,
    get_bracket_order_status,
    research_stock,
]
