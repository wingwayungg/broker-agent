"""Turns a graph run into the API's SSE events (schema in api/schemas.py)."""

import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

TOOL_LABELS = {
    "get_positions": "Checking your positions",
    "get_account_summary": "Checking your account",
    "get_open_orders": "Checking open orders",
    "get_recent_fills": "Checking recent fills",
    "get_bracket_order_status": "Checking bracket orders",
    "research_stock": "Researching",
    "search_filings": "Searching 10-K filings",
    "buy_stock": "Preparing your order",
}


def tool_label(call: dict[str, Any]) -> str:
    name: str = call["name"]
    label = TOOL_LABELS.get(name) or name
    symbol = name == "research_stock" and call.get("args", {}).get("symbol")
    return f"{label} {str(symbol).upper()}" if symbol else label


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def run_events(
    graph: CompiledStateGraph, graph_input: Any, config: RunnableConfig
) -> AsyncIterator[tuple[str, Any]]:
    """The run's token/status/interrupt events, in order.

    "messages" mode carries every LLM call in the graph, including the sub-agent and
    synthesis calls research_stock makes inside the tools node. Those are working
    notes, not the reply, so only tokens from the agent node are passed on.
    """
    chunk: Any  # (message, metadata) in "messages" mode, {node: update} in "updates"
    async for mode, chunk in graph.astream(
        graph_input, config, stream_mode=["messages", "updates"]
    ):
        if mode == "messages":
            message, metadata = chunk
            if (
                metadata.get("langgraph_node") == "agent"
                and isinstance(message, AIMessageChunk)
                and isinstance(message.content, str)
                and message.content
            ):
                yield "token", {"id": message.id, "text": message.content}
        elif "__interrupt__" in chunk:
            yield "interrupt", chunk["__interrupt__"][0].value
        elif (update := chunk.get("agent")) and (calls := update["messages"][-1].tool_calls):
            # From the node's finished message rather than the token stream, where
            # tool-call arguments arrive in fragments.
            yield "status", {"label": tool_label(calls[0])}
